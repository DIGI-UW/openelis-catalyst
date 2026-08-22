import { ArrowRight } from "@carbon/icons-react";
import { Button, Form, Select, SelectItem, TextArea } from "@carbon/react";
import { type FormEvent } from "react";
import type { QueryProfile } from "../types";

interface QuestionFormProps {
  question: string;
  busy: boolean;
  disabled?: boolean;
  onQuestionChange: (question: string) => void;
  onSubmit: (question: string) => void;
  profiles?: QueryProfile[];
  selectedProfileId?: string;
  onProfileChange?: (profileId: string) => void;
}

const profileModelAliases = (profile: QueryProfile) =>
  Array.from(
    new Set(
      Object.entries(profile.roleModels)
        .sort(([leftRole], [rightRole]) =>
          leftRole < rightRole ? -1 : leftRole > rightRole ? 1 : 0,
        )
        .map(([, modelAlias]) => modelAlias.trim())
        .filter(Boolean),
    ),
  );

export const QuestionForm = ({
  question,
  busy,
  disabled = false,
  onQuestionChange,
  onSubmit,
  profiles = [],
  selectedProfileId,
  onProfileChange,
}: QuestionFormProps) => {
  const normalizedQuestion = question.trim();
  const availableProfiles = profiles.filter((profile) => profile.available);
  // Which concrete models the selected profile actually runs. The recordings
  // and the published cuts lean on this being on screen, so it stays visible
  // even though it no longer crowds the option text.
  const selectedAliases = profileModelAliases(
    availableProfiles.find((profile) => profile.id === selectedProfileId) ??
      availableProfiles[0] ?? { roleModels: {} } as QueryProfile,
  );

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!normalizedQuestion || busy || disabled) return;
    onSubmit(normalizedQuestion);
  };

  return (
    <section
      id="ask-openelis"
      className="query-card query-card--question"
      aria-label="Query composer"
    >
      {/*
        No heading. This used to read "Ask OpenELIS" whatever catalog the
        session was grounded in -- wrong product on an OpenMRS session, and the
        wrong relationship in any case: you ask questions about data that came
        from a source, not the source itself. The surrounding empty state
        already names the active source ("Ask a question about ..."), so the
        honest fix is one label, not two.
      */}
      <Form className="query-composer-form" onSubmit={handleSubmit}>
        <div className="query-composer">
          <div className="query-composer__input">
            {/*
              The label is carried for assistive tech but not drawn: on a wide
              composer a lone "Question" sat in the top-left corner of the box
              with nothing beside it, reading as a stray fragment rather than a
              field label. The placeholder and the surrounding empty state
              already say what goes here.
            */}
            <TextArea
              id="catalyst-question"
              labelText="Question"
              hideLabel
              placeholder="Describe the data you want to explore"
              value={question}
              rows={2}
              autoFocus
              disabled={busy || disabled}
              onChange={(event) => onQuestionChange(event.currentTarget.value)}
            />
          </div>
          <div className="query-composer__toolbar">
            {availableProfiles.length > 0 && (
              /*
               * Carbon's Select, not a bare <select> wearing a bottom border:
               * the hand-rolled one missed the chevron, the layer background,
               * the focus ring, and the disabled treatment that every other
               * control on the page gets for free.
               */
              <Select
                id="catalyst-profile"
                className="query-composer__profile"
                labelText="Model profile"
                size="sm"
                value={selectedProfileId}
                disabled={busy || disabled}
                helperText={
                  selectedAliases.length > 0
                    ? selectedAliases.join(" · ")
                    : undefined
                }
                onChange={(event) => onProfileChange?.(event.currentTarget.value)}
              >
                {availableProfiles.map((profile) => (
                  /*
                   * profile.label already names the models in prose ("Gemma 4
                   * 12B writer, Qwen 2.5 14B reviewer"); appending the aliases
                   * repeated it in slug form and overflowed the control. The
                   * aliases are disclosed under the field instead.
                   */
                  <SelectItem
                    key={profile.id}
                    value={profile.id}
                    text={profile.label}
                  />
                ))}
              </Select>
            )}
            {profiles.length > 0 && availableProfiles.length === 0 && (
              <p className="query-composer__availability" role="status">
                No configured model profile is currently available.
              </p>
            )}
            <Button
              type="submit"
              renderIcon={ArrowRight}
              disabled={!normalizedQuestion || busy || disabled}
            >
              {busy ? "Generating query…" : "Generate query"}
            </Button>
          </div>
        </div>
      </Form>
    </section>
  );
};
