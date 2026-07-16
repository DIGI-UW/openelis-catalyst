import { Renew } from "@carbon/icons-react";
import { Button, InlineLoading, InlineNotification } from "@carbon/react";
import type { ReactNode } from "react";

interface ExecutionStateProps {
  title: string;
  message: string;
  kind?: "info" | "warning" | "error" | "success";
  running?: boolean;
  details?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}

export const ExecutionState = ({
  title,
  message,
  kind = "info",
  running = false,
  details,
  actionLabel,
  onAction,
}: ExecutionStateProps) => (
  <section className="query-card execution-state" aria-labelledby="execution-title">
    <div className="execution-state__heading">
      <div>
        <p className="eyebrow">Catalyst status</p>
        <h2 id="execution-title">{title}</h2>
      </div>
      {running && (
        <InlineLoading
          className="execution-state__loading"
          description="Polling for results"
          status="active"
        />
      )}
    </div>
    <InlineNotification
      lowContrast
      hideCloseButton
      kind={kind}
      title={message}
    />
    {details}
    {actionLabel && onAction && (
      <Button kind="tertiary" renderIcon={Renew} onClick={onAction}>
        {actionLabel}
      </Button>
    )}
  </section>
);
