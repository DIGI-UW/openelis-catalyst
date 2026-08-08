import { ArrowDown, ArrowUp } from "@carbon/icons-react";
import { Button } from "@carbon/react";
import { useEffect, useRef, useState } from "react";

type JumpDirection = "down" | "up";

const COMPACT_VIEWPORT_QUERY = "(max-width: 28rem)";

const isCompactViewport = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia(COMPACT_VIEWPORT_QUERY).matches;

const getComposerElements = () => {
  const followupInput = document.getElementById("catalyst-followup");
  const hasFollowup = followupInput instanceof HTMLTextAreaElement;
  const followupIsHidden = hasFollowup && followupInput.closest("[hidden]") !== null;
  const followupToggle = document.getElementById("refine-openelis-toggle");
  const target = hasFollowup
    ? followupIsHidden
      ? followupToggle
      : followupInput
    : document.getElementById("catalyst-question");
  const section = document.getElementById(
    hasFollowup ? "refine-openelis" : "ask-openelis",
  );
  const heading = document.getElementById(
    hasFollowup ? "refine-query-title" : "question-title",
  );

  return {
    target: target instanceof HTMLElement ? target : null,
    section: section instanceof HTMLElement ? section : null,
    heading: heading instanceof HTMLElement ? heading : null,
    // This control names where it takes you, which is the composer. The
    // refine heading already says that ("Refine [4]"); the page heading names
    // the session, so borrowing it would put the wrong word on the button.
    label: hasFollowup
      ? heading?.textContent?.trim() || "Refine the current query"
      : "Ask a question",
  };
};

export const AskOpenElisNavigation = () => {
  const [inputIsVisible, setInputIsVisible] = useState(false);
  const [jumpDirection, setJumpDirection] = useState<JumpDirection>("down");
  const [jumpHasFocus, setJumpHasFocus] = useState(false);
  const [compactViewport, setCompactViewport] = useState(isCompactViewport);
  const [targetId, setTargetId] = useState("catalyst-question");
  const [targetLabel, setTargetLabel] = useState("Ask a question");
  const observedTargetRef = useRef<HTMLElement | null>(null);
  const jumpIsExposed = compactViewport || !inputIsVisible || jumpHasFocus;

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;

    const compactMedia = window.matchMedia(COMPACT_VIEWPORT_QUERY);
    const updateCompactViewport = (event: MediaQueryListEvent) => {
      setCompactViewport(event.matches);
    };
    compactMedia.addEventListener?.("change", updateCompactViewport);
    return () => {
      compactMedia.removeEventListener?.("change", updateCompactViewport);
    };
  }, []);

  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;

    const intersectionObserver = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        setInputIsVisible(entry.isIntersecting);
        if (!entry.isIntersecting) {
          setJumpDirection(entry.boundingClientRect.top < 0 ? "up" : "down");
        }
      },
      { rootMargin: "-48px 0px -24px", threshold: 0.2 },
    );
    const bindCanonicalComposer = () => {
      const { label, target } = getComposerElements();
      if (!target) return;
      setTargetId(target.id);
      setTargetLabel(label);
      if (observedTargetRef.current === target) return;
      intersectionObserver.disconnect();
      observedTargetRef.current = target;
      setInputIsVisible(false);
      intersectionObserver.observe(target);
    };

    bindCanonicalComposer();
    const mutationObserver = typeof MutationObserver === "undefined"
      ? null
      : new MutationObserver(bindCanonicalComposer);
    mutationObserver?.observe(document.body, {
      characterData: true,
      childList: true,
      attributes: true,
      subtree: true,
    });
    return () => {
      mutationObserver?.disconnect();
      intersectionObserver.disconnect();
      observedTargetRef.current = null;
    };
  }, []);

  const focusComposer = () => {
    const { heading, section, target } = getComposerElements();
    if (!target) return;

    const prefersReducedMotion =
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    if (typeof section?.scrollIntoView === "function") {
      section.scrollIntoView({
        behavior: prefersReducedMotion ? "auto" : "smooth",
        block: "center",
      });
    }

    const focusTarget =
      target instanceof HTMLTextAreaElement && target.disabled ? heading : target;
    focusTarget?.focus({ preventScroll: true });
  };

  return (
    <nav
      className="ask-openelis-navigation"
      aria-label="Ask OpenELIS navigation"
      aria-hidden={!jumpIsExposed}
      data-compact={compactViewport}
      hidden={!jumpIsExposed}
    >
      <Button
        className="ask-openelis-jump"
        data-testid="ask-openelis-jump"
        data-direction={jumpDirection}
        data-visible={jumpIsExposed}
        kind="secondary"
        size="sm"
        renderIcon={jumpDirection === "up" ? ArrowUp : ArrowDown}
        aria-controls={targetId}
        aria-describedby="ask-openelis-direction"
        aria-hidden={!jumpIsExposed}
        tabIndex={jumpIsExposed ? 0 : -1}
        onFocus={() => setJumpHasFocus(true)}
        onBlur={() => setJumpHasFocus(false)}
        onClick={focusComposer}
      >
        {targetLabel}
      </Button>
      <span id="ask-openelis-direction" className="visually-hidden">
        {inputIsVisible
          ? "The composer is currently visible."
          : `The composer is ${jumpDirection === "up" ? "above" : "below"}.`}
      </span>
    </nav>
  );
};
