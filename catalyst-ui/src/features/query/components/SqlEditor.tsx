import { PostgreSQL, sql } from "@codemirror/lang-sql";
import { Compartment, EditorState } from "@codemirror/state";
import { Button } from "@carbon/react";
import { basicSetup, EditorView } from "codemirror";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import "./SqlEditor.css";
import {
  buildSqlCompletionSchema,
  formatPostgresqlSql,
  type SqlCatalogRelation,
} from "./sqlEditorSupport";

export interface SqlEditorProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  catalog?: readonly SqlCatalogRelation[];
  readOnly?: boolean;
  wrapLines?: boolean;
  onWrapLinesChange?: (wrapLines: boolean) => void;
}

const EMPTY_CATALOG: readonly SqlCatalogRelation[] = [];

export const SqlEditor = ({
  label,
  value,
  onChange,
  catalog = EMPTY_CATALOG,
  readOnly = false,
  wrapLines: controlledWrapLines,
  onWrapLinesChange,
}: SqlEditorProps) => {
  const labelId = useId();
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const onChangeRef = useRef(onChange);
  const applyingExternalValueRef = useRef(false);
  const wrappingCompartmentRef = useRef(new Compartment());
  const languageCompartmentRef = useRef(new Compartment());
  const attributesCompartmentRef = useRef(new Compartment());
  const readOnlyCompartmentRef = useRef(new Compartment());
  const initialConfigurationRef = useRef({
    labelId,
    readOnly,
    value,
    wrapLines: controlledWrapLines ?? true,
    schema: buildSqlCompletionSchema(catalog),
  });
  const [internalWrapLines, setInternalWrapLines] = useState(true);
  const [status, setStatus] = useState("");
  const wrapLines = controlledWrapLines ?? internalWrapLines;
  const completionSchema = useMemo(
    () => buildSqlCompletionSchema(catalog),
    [catalog],
  );

  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    const parent = hostRef.current;
    if (!parent) return;
    const initial = initialConfigurationRef.current;
    const view = new EditorView({
      parent,
      state: EditorState.create({
        doc: initial.value,
        extensions: [
          basicSetup,
          languageCompartmentRef.current.of(
            sql({
              dialect: PostgreSQL,
              schema: initial.schema,
              upperCaseKeywords: true,
            }),
          ),
          wrappingCompartmentRef.current.of(
            initial.wrapLines ? EditorView.lineWrapping : [],
          ),
          attributesCompartmentRef.current.of(
            EditorView.contentAttributes.of({
              "aria-labelledby": initial.labelId,
              "aria-multiline": "true",
              spellcheck: "false",
            }),
          ),
          readOnlyCompartmentRef.current.of([
            EditorState.readOnly.of(initial.readOnly),
            EditorView.editable.of(!initial.readOnly),
          ]),
          EditorView.updateListener.of((update) => {
            if (update.docChanged && !applyingExternalValueRef.current) {
              onChangeRef.current(update.state.doc.toString());
            }
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      viewRef.current = null;
      view.destroy();
    };
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    applyingExternalValueRef.current = true;
    view.dispatch({ changes: { from: 0, to: current.length, insert: value } });
    applyingExternalValueRef.current = false;
  }, [value]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: wrappingCompartmentRef.current.reconfigure(
        wrapLines ? EditorView.lineWrapping : [],
      ),
    });
  }, [wrapLines]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: languageCompartmentRef.current.reconfigure(
        sql({
          dialect: PostgreSQL,
          schema: completionSchema,
          upperCaseKeywords: true,
        }),
      ),
    });
  }, [completionSchema]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: attributesCompartmentRef.current.reconfigure(
        EditorView.contentAttributes.of({
          "aria-labelledby": labelId,
          "aria-multiline": "true",
          spellcheck: "false",
        }),
      ),
    });
  }, [labelId]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: readOnlyCompartmentRef.current.reconfigure([
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
      ]),
    });
  }, [readOnly]);

  const toggleWrapping = () => {
    const nextValue = !wrapLines;
    if (controlledWrapLines === undefined) setInternalWrapLines(nextValue);
    onWrapLinesChange?.(nextValue);
  };

  const formatSql = () => {
    const view = viewRef.current;
    if (!view || readOnly) return;
    try {
      const current = view.state.doc.toString();
      const formatted = formatPostgresqlSql(current);
      if (formatted === current) {
        setStatus("SQL is already formatted.");
        return;
      }
      view.dispatch({
        changes: { from: 0, to: current.length, insert: formatted },
      });
      setStatus("SQL formatted.");
    } catch (error) {
      setStatus(
        error instanceof Error ? `Format failed: ${error.message}` : "Format failed.",
      );
    }
  };

  return (
    <div className="sql-editor" data-language="postgresql">
      <div id={labelId} className="sql-editor__label">
        {label}
      </div>
      <div ref={hostRef} className="sql-editor__surface" />
      <div className="sql-editor__toolbar" aria-label="SQL editor controls">
        <Button
          type="button"
          kind="ghost"
          size="sm"
          aria-pressed={wrapLines}
          disabled={readOnly}
          onClick={toggleWrapping}
        >
          Wrap lines
        </Button>
        <Button
          type="button"
          kind="ghost"
          size="sm"
          disabled={readOnly}
          onClick={formatSql}
        >
          Format SQL
        </Button>
      </div>
      <p className="sql-editor__status" aria-live="polite">
        {status}
      </p>
    </div>
  );
};
