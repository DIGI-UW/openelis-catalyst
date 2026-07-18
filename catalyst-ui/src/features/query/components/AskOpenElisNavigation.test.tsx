import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../../../App";
import type { CatalystApi } from "../api";

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
});
