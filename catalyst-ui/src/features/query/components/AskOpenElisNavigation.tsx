import { ArrowDown, ArrowUp } from "@carbon/icons-react";
import { Button } from "@carbon/react";
import { useEffect, useState } from "react";

type JumpDirection = "down" | "up";

const getComposerElements = () => {
  const input = document.getElementById("catalyst-question");
  const section = document.getElementById("ask-openelis");
  const heading = document.getElementById("question-title");

  return {
    input: input instanceof HTMLTextAreaElement ? input : null,
    section: section instanceof HTMLElement ? section : null,
    heading: heading instanceof HTMLElement ? heading : null,
  };
};

export const AskOpenElisNavigation = () => {
  const [inputIsVisible, setInputIsVisible] = useState(false);
  const [jumpDirection, setJumpDirection] = useState<JumpDirection>("down");
  const [jumpHasFocus, setJumpHasFocus] = useState(false);
  const jumpIsExposed = !inputIsVisible || jumpHasFocus;

  useEffect(() => {
    const { input } = getComposerElements();
    if (!input || typeof IntersectionObserver === "undefined") return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry) return;
        setInputIsVisible(entry.isIntersecting);
        if (!entry.isIntersecting) {
          setJumpDirection(entry.boundingClientRect.top < 0 ? "up" : "down");
        }
      },
      { rootMargin: "-48px 0px -24px", threshold: 0.2 },
    );
    observer.observe(input);
    return () => observer.disconnect();
  }, []);

  const focusComposer = () => {
    const { heading, input, section } = getComposerElements();
    if (!input) return;

    const prefersReducedMotion =
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    if (typeof section?.scrollIntoView === "function") {
      section.scrollIntoView({
        behavior: prefersReducedMotion ? "auto" : "smooth",
        block: "center",
      });
    }

    const target = input.disabled ? heading : input;
    target?.focus({ preventScroll: true });
  };

  return (
    <nav
      className="ask-openelis-navigation"
      aria-label="Ask OpenELIS navigation"
      aria-hidden={!jumpIsExposed}
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
        aria-controls="catalyst-question"
        aria-describedby="ask-openelis-direction"
        aria-hidden={!jumpIsExposed}
        tabIndex={jumpIsExposed ? 0 : -1}
        onFocus={() => setJumpHasFocus(true)}
        onBlur={() => setJumpHasFocus(false)}
        onClick={focusComposer}
      >
        Ask OpenELIS
      </Button>
      <span id="ask-openelis-direction" className="visually-hidden">
        The composer is {jumpDirection === "up" ? "above" : "below"}.
      </span>
    </nav>
  );
};
