import { ArrowRight } from "@carbon/icons-react";
import { Button, Form, TextArea } from "@carbon/react";
import { type FormEvent } from "react";
import type { DataSource, QueryProfile } from "../types";

interface QuestionFormProps {
  question: string;
  busy: boolean;
  disabled?: boolean;
  onQuestionChange: (question: string) => void;
  onSubmit: (question: string) => void;
  profiles?: QueryProfile[];
  selectedProfileId?: string;
  onProfileChange?: (profileId: string) => void;
  dataSources?: DataSource[];
  selectedDataSourceId?: string;
  onDataSourceChange?: (dataSourceId: string) => void;
}

const profileOptionLabel = (profile: QueryProfile) => {
  const modelAliases = Array.from(
    new Set(
      Object.entries(profile.roleModels)
        .sort(([leftRole], [rightRole]) =>
          leftRole < rightRole ? -1 : leftRole > rightRole ? 1 : 0,
        )
        .map(([, modelAlias]) => modelAlias.trim())
        .filter(Boolean),
    ),
  );

  return modelAliases.length > 0
    ? `${profile.label} — ${modelAliases.join(", ")}`
    : profile.label;
};

export const QuestionForm = ({
  question,
  busy,
  disabled = false,
  onQuestionChange,
  onSubmit,
  profiles = [],
  selectedProfileId,
  onProfileChange,
  dataSources = [],
  selectedDataSourceId,
  onDataSourceChange,
}: QuestionFormProps) => {
  const normalizedQuestion = question.trim();
  const availableProfiles = profiles.filter((profile) => profile.available);
  const availableSources = dataSources.filter((source) => source.available);

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
      <div className="section-heading query-composer__heading">
        <strong>Ask OpenELIS</strong>
      </div>
      <Form className="query-composer-form" onSubmit={handleSubmit}>
        <div className="query-composer">
          <div className="query-composer__input">
            <TextArea
              id="catalyst-question"
              labelText="Question"
              placeholder="Describe the laboratory data you want to explore"
              value={question}
              rows={2}
              autoFocus
              disabled={busy || disabled}
              onChange={(event) => onQuestionChange(event.currentTarget.value)}
            />
          </div>
          <div className="query-composer__toolbar">
            {/*
              A session is grounded in one catalog: its queries and versions
              cannot move to another source later, so the source is chosen
              here, at creation, and displayed read-only in the rail after.
            */}
            {availableSources.length > 1 && (
              <label className="profile-selector" htmlFor="catalyst-data-source">
                <span>Data source</span>
                <select
                  id="catalyst-data-source"
                  value={selectedDataSourceId}
                  disabled={busy || disabled}
                  onChange={(event) =>
                    onDataSourceChange?.(event.currentTarget.value)
                  }
                >
                  {availableSources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {availableProfiles.length > 0 && (
              <label className="profile-selector" htmlFor="catalyst-profile">
                <span>Model profile</span>
                <select
                  id="catalyst-profile"
                  value={selectedProfileId}
                  disabled={busy || disabled}
                  onChange={(event) => onProfileChange?.(event.currentTarget.value)}
                >
                  {availableProfiles.map((profile) => (
                    <option
                      key={profile.id}
                      value={profile.id}
                    >
                      {profileOptionLabel(profile)}
                    </option>
                  ))}
                </select>
              </label>
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
