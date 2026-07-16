import { ArrowRight } from "@carbon/icons-react";
import { Button, Form, Stack, TextArea } from "@carbon/react";
import type { FormEvent } from "react";

interface QuestionFormProps {
  question: string;
  busy: boolean;
  disabled?: boolean;
  onQuestionChange: (question: string) => void;
  onSubmit: (question: string) => void;
}

export const QuestionForm = ({
  question,
  busy,
  disabled = false,
  onQuestionChange,
  onSubmit,
}: QuestionFormProps) => {
  const normalizedQuestion = question.trim();

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!normalizedQuestion || busy || disabled) return;
    onSubmit(normalizedQuestion);
  };

  return (
    <section className="query-card query-card--question" aria-labelledby="question-title">
      <div className="section-heading">
        <p className="eyebrow">Natural language to governed data</p>
        <h1 id="question-title">Ask OpenELIS data</h1>
        <p>
          Catalyst prepares a read-only query for review. Nothing runs until you
          explicitly accept the preview.
        </p>
      </div>
      <Form onSubmit={handleSubmit}>
        <Stack gap={6}>
          <TextArea
            id="catalyst-question"
            labelText="Question"
            helperText="Ask for a table using demo analytics data."
            placeholder="For example: Show recent viral load results"
            value={question}
            rows={3}
            disabled={busy || disabled}
            onChange={(event) => onQuestionChange(event.currentTarget.value)}
          />
          <Button
            type="submit"
            renderIcon={ArrowRight}
            disabled={!normalizedQuestion || busy || disabled}
          >
            {busy ? "Generating preview…" : "Generate preview"}
          </Button>
        </Stack>
      </Form>
    </section>
  );
};
