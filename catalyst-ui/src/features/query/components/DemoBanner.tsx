import { WarningAltFilled } from "@carbon/icons-react";
import { Tag } from "@carbon/react";
import { AskOpenElisNavigation } from "./AskOpenElisNavigation";

export const DemoBanner = () => (
  <aside className="demo-banner" aria-label="Demo environment notice">
    <div className="demo-banner__inner">
      <WarningAltFilled aria-hidden size={20} />
      <Tag type="warm-gray" size="sm">
        Demo environment
      </Tag>
      <span className="demo-banner__message">
        Demo data only; not for clinical decision-making.
      </span>
      <AskOpenElisNavigation />
    </div>
  </aside>
);
