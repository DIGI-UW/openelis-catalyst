import { PlayFilledAlt } from "@carbon/icons-react";
import {
  Button,
  CodeSnippet,
  InlineNotification,
  StructuredListBody,
  StructuredListCell,
  StructuredListHead,
  StructuredListRow,
  StructuredListWrapper,
  Tag,
} from "@carbon/react";
import type { BoundParameter, CatalystPreview } from "../types";

interface QueryPreviewProps {
  preview: CatalystPreview;
  executing: boolean;
  onAccept: () => void;
}

const formatValue = (parameter: BoundParameter) => {
  if (Array.isArray(parameter.value)) return JSON.stringify(parameter.value);
  if (typeof parameter.value === "boolean") {
    return parameter.value ? "true" : "false";
  }
  return String(parameter.value);
};

const formatExpiry = (value: string) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(new Date(value));

export const QueryPreview = ({
  preview,
  executing,
  onAccept,
}: QueryPreviewProps) => (
  <section className="query-card query-preview" aria-labelledby="preview-title">
    <div className="section-heading section-heading--row">
      <div>
        <p className="eyebrow">Explicit acceptance required</p>
        <h2 id="preview-title">Review query</h2>
      </div>
      <Tag type="blue">Read only</Tag>
    </div>

    <dl className="preview-metadata">
      <div>
        <dt>Data source</dt>
        <dd>{preview.target.dataSource}</dd>
      </div>
      <div>
        <dt>Catalog</dt>
        <dd>{preview.target.catalogVersion}</dd>
      </div>
      <div>
        <dt>Dialect</dt>
        <dd>{preview.target.dialect}</dd>
      </div>
      <div>
        <dt>Expires</dt>
        <dd>
          <time dateTime={preview.expiresAt}>{formatExpiry(preview.expiresAt)}</time>
        </dd>
      </div>
    </dl>

    <div className="preview-block">
      <h3>SQL</h3>
      <div aria-label="Generated SQL">
        <CodeSnippet type="multi" feedback="Copied">
          {preview.sql}
        </CodeSnippet>
      </div>
    </div>

    <div className="preview-block">
      <h3>Typed parameters</h3>
      {preview.parameters.length === 0 ? (
        <p className="muted">No bound parameters.</p>
      ) : (
        <StructuredListWrapper aria-label="Typed query parameters">
          <StructuredListHead>
            <StructuredListRow head>
              <StructuredListCell head>Name</StructuredListCell>
              <StructuredListCell head>Type</StructuredListCell>
              <StructuredListCell head>Value</StructuredListCell>
            </StructuredListRow>
          </StructuredListHead>
          <StructuredListBody>
            {preview.parameters.map((parameter) => (
              <StructuredListRow key={parameter.name}>
                <StructuredListCell>{parameter.name}</StructuredListCell>
                <StructuredListCell>
                  <Tag size="sm" type="cool-gray">
                    {parameter.type}
                  </Tag>
                </StructuredListCell>
                <StructuredListCell>{formatValue(parameter)}</StructuredListCell>
              </StructuredListRow>
            ))}
          </StructuredListBody>
        </StructuredListWrapper>
      )}
    </div>

    <InlineNotification
      lowContrast
      hideCloseButton
      kind="warning"
      title="Review before running"
      subtitle="Accepting runs this exact digest once, subject to expiry and Catalyst policy."
    />
    <div className="preview-actions">
      <Button renderIcon={PlayFilledAlt} disabled={executing} onClick={onAccept}>
        {executing ? "Starting query…" : "Accept and run"}
      </Button>
    </div>
  </section>
);
