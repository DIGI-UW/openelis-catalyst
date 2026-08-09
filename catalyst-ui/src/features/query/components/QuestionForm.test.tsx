import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QuestionForm } from "./QuestionForm";

/*
 * The composer carried a heading that read "Ask OpenELIS" regardless of which
 * catalog the session was grounded in, so an OpenMRS session announced the
 * wrong product -- directly above an empty state that already named the right
 * one. It was removed rather than corrected: the surrounding view names the
 * active source, so a second label restated it at best and contradicted it at
 * worst.
 */
const noop = () => {};

const renderForm = () =>
  render(
    <QuestionForm
      question=""
      busy={false}
      onQuestionChange={noop}
      onSubmit={vi.fn()}
    />,
  );

describe("QuestionForm", () => {
  it("does not name a data source of its own", () => {
    renderForm();
    expect(screen.queryByText(/Ask OpenELIS/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Ask /i)).not.toBeInTheDocument();
  });

  it("keeps the question placeholder source-neutral", () => {
    renderForm();
    expect(screen.getByLabelText("Question")).toHaveAttribute(
      "placeholder",
      "Describe the data you want to explore",
    );
  });

  it("still renders the question input and submit affordance", () => {
    renderForm();
    expect(screen.getByLabelText("Question")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Generate query" }),
    ).toBeInTheDocument();
  });
});
