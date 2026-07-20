import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../../App";
import type { CatalystApi } from "../api";
import { AskOpenElisNavigation } from "./AskOpenElisNavigation";

const makeApi = (): CatalystApi => ({
  submitQuestion: vi.fn(),
  executePreview: vi.fn(),
  pollExecution: vi.fn(),
});

const rectangle = (top: number): DOMRectReadOnly =>
  ({
    x: 0,
    y: top,
    top,
    right: 800,
    bottom: top + 160,
    left: 0,
    width: 800,
    height: 160,
    toJSON: () => ({}),
  }) as DOMRectReadOnly;

class IntersectionObserverMock {
  static instances: IntersectionObserverMock[] = [];

  readonly root = null;
  readonly rootMargin: string;
  readonly thresholds: readonly number[];
  private readonly callback: IntersectionObserverCallback;
  private observedTarget: Element | null = null;

  constructor(
    callback: IntersectionObserverCallback,
    options: IntersectionObserverInit = {},
  ) {
    this.callback = callback;
    this.rootMargin = options.rootMargin ?? "0px";
    this.thresholds = Array.isArray(options.threshold)
      ? options.threshold
      : [options.threshold ?? 0];
    IntersectionObserverMock.instances.push(this);
  }

  observe = vi.fn((target: Element) => {
    this.observedTarget = target;
  });

  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn((): IntersectionObserverEntry[] => []);

  emit({ isIntersecting, top }: { isIntersecting: boolean; top: number }) {
    if (!this.observedTarget) throw new Error("No composer input was observed.");
    const bounds = rectangle(top);
    const entry = {
      boundingClientRect: bounds,
      intersectionRatio: isIntersecting ? 1 : 0,
      intersectionRect: isIntersecting ? bounds : rectangle(0),
      isIntersecting,
      rootBounds: rectangle(0),
      target: this.observedTarget,
      time: 0,
    } as IntersectionObserverEntry;
    this.callback(
      [entry],
      this as unknown as IntersectionObserver,
    );
  }
}

const emitIntersection = (isIntersecting: boolean, top: number) => {
  const observer = IntersectionObserverMock.instances[0];
  if (!observer) throw new Error("Ask OpenELIS observer was not created.");
  act(() => observer.emit({ isIntersecting, top }));
};

describe("Ask OpenELIS reachability navigation", () => {
  beforeEach(() => {
    IntersectionObserverMock.instances = [];
    vi.stubGlobal(
      "IntersectionObserver",
      IntersectionObserverMock as unknown as typeof IntersectionObserver,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("only exposes the control offscreen and reports whether the composer is above or below", () => {
    render(<App api={makeApi()} />);

    const jump = screen.getByTestId("ask-openelis-jump");
    expect(jump).toBeVisible();
    expect(jump).toHaveAttribute("data-direction", "down");

    emitIntersection(true, 120);
    expect(jump).not.toBeVisible();
    expect(jump).toHaveAttribute("aria-hidden", "true");

    emitIntersection(false, 900);
    expect(jump).toBeVisible();
    expect(jump).toHaveAttribute("data-direction", "down");
    expect(jump).toHaveAccessibleDescription("The composer is below.");

    emitIntersection(false, -220);
    expect(jump).toHaveAttribute("data-direction", "up");
    expect(jump).toHaveAccessibleDescription("The composer is above.");
  });

  it("keeps the same control mounted and visible if intersection changes while it has focus", () => {
    render(<App api={makeApi()} />);
    emitIntersection(false, 900);

    const jump = screen.getByTestId("ask-openelis-jump");
    act(() => jump.focus());
    expect(jump).toHaveFocus();

    emitIntersection(true, 120);
    expect(jump).toHaveFocus();
    expect(jump).toBeVisible();
    expect(jump).toHaveAttribute("data-visible", "true");
    expect(document.body.contains(jump)).toBe(true);

    act(() => jump.blur());
    expect(jump).not.toBeVisible();
    expect(document.body.contains(jump)).toBe(true);
  });

  it("keeps the canonical jump available when the compact viewport contains the composer", () => {
    let compactViewportListener: ((event: MediaQueryListEvent) => void) | null =
      null;
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: false,
        media: query,
        addEventListener: vi.fn(
          (eventName: string, listener: (event: MediaQueryListEvent) => void) => {
            if (query === "(max-width: 28rem)" && eventName === "change") {
              compactViewportListener = listener;
            }
          },
        ),
        removeEventListener: vi.fn(),
      })),
    );
    render(<App api={makeApi()} />);

    const navigation = screen.getByRole("navigation", {
      name: "Ask OpenELIS navigation",
    });
    const jump = screen.getByTestId("ask-openelis-jump");
    emitIntersection(true, 120);
    expect(jump).not.toBeVisible();

    act(() => {
      compactViewportListener?.({ matches: true } as MediaQueryListEvent);
    });

    expect(navigation).not.toHaveAttribute("hidden");
    expect(navigation).toHaveAttribute("data-compact", "true");
    expect(jump).toBeVisible();
    expect(jump).toHaveAttribute("tabindex", "0");
    expect(jump).toHaveAccessibleDescription(
      "The composer is currently visible.",
    );
    expect(document.querySelectorAll("#catalyst-question")).toHaveLength(1);
  });

  it("centers and focuses the canonical textarea while respecting reduced motion", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: true }),
    );
    render(<App api={makeApi()} />);
    emitIntersection(false, -220);

    const section = document.getElementById("ask-openelis");
    const scrollIntoView = vi.fn();
    Object.defineProperty(section, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    const jump = screen.getByRole("button", { name: "Ask OpenELIS" });

    await user.click(jump);

    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "auto",
      block: "center",
    });
    expect(screen.getByLabelText("Question")).toHaveFocus();
    expect(document.body.contains(jump)).toBe(true);
  });

  it("targets the follow-up composer when a notebook session is active", async () => {
    const user = userEvent.setup();
    render(
      <>
        <section id="ask-openelis">
          <h1 id="question-title">Ask OpenELIS</h1>
          <textarea id="catalyst-question" aria-label="Question" disabled />
        </section>
        <section id="refine-openelis">
          <h2 id="refine-query-title">Refine Query v2</h2>
          <textarea
            id="catalyst-followup"
            aria-label="Follow-up instruction"
          />
        </section>
        <AskOpenElisNavigation />
      </>,
    );

    const observer = IntersectionObserverMock.instances[0];
    expect(observer?.observe).toHaveBeenCalledWith(
      document.getElementById("catalyst-followup"),
    );
    emitIntersection(false, 900);
    const jump = screen.getByRole("button", { name: "Refine Query v2" });
    expect(jump).toHaveAttribute("aria-controls", "catalyst-followup");
    expect(jump).toHaveAccessibleDescription(/below/i);
    jump.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByLabelText("Follow-up instruction")).toHaveFocus();
  });

  it("refreshes the sticky label when the mounted follow-up heading changes", async () => {
    const renderNotebook = (version: number) => (
      <>
        <section id="refine-openelis">
          <h2 id="refine-query-title">Refine Query v{version}</h2>
          <textarea
            id="catalyst-followup"
            aria-label="Follow-up instruction"
          />
        </section>
        <AskOpenElisNavigation />
      </>
    );
    const { rerender } = render(renderNotebook(2));
    const followupInput = document.getElementById("catalyst-followup");

    expect(screen.getByRole("button", { name: "Refine Query v2" })).toBeVisible();

    rerender(renderNotebook(3));

    expect(document.getElementById("catalyst-followup")).toBe(followupInput);
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "Refine Query v3" }),
      ).toBeVisible();
    });
  });
});
