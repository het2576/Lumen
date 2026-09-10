"use client";

import { Fragment, useState } from "react";
import LumenChart, { ChartDataPoint } from "./LumenChart";

// ─── Inline text renderer (bold, etc.) ───────────────────────────────────────
function InlineText({ text }: { text: string }) {
  // Handle [n] citation markers
  const parts = text.split(/(\*\*[^*]+\*\*|\[\d+\])/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**"))
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        if (/^\[\d+\]$/.test(part))
          return (
            <sup key={i} className="citation-ref">
              {part}
            </sup>
          );
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

// ─── Chart block parser ────────────────────────────────────────────────────────
interface ChartBlock {
  type: "bar" | "line" | "donut" | "pie";
  title?: string;
  subtitle?: string;
  data: ChartDataPoint[];
}

function parseChartBlock(raw: string): ChartBlock | null {
  try {
    const parsed = JSON.parse(raw.trim());
    if (!parsed.data || !Array.isArray(parsed.data)) return null;
    return parsed as ChartBlock;
  } catch {
    return null;
  }
}

// ─── Smart table with auto-chart ──────────────────────────────────────────────
function extractNumericSeries(
  headers: string[],
  rows: string[][]
): ChartDataPoint[] | null {
  // Find the first numeric column
  for (let col = 1; col < headers.length; col++) {
    const vals = rows.map((r) => {
      const raw = (r[col] || "").replace(/[₹$€£,\s%]/g, "");
      return parseFloat(raw);
    });
    if (vals.every((v) => !isNaN(v))) {
      return rows.map((r, i) => ({
        label: r[0] || `Row ${i + 1}`,
        value: vals[i],
        formattedValue: r[col],
      }));
    }
  }
  return null;
}

function SmartTable({
  headers,
  rows,
}: {
  headers: string[];
  rows: string[][];
}) {
  const series = extractNumericSeries(headers, rows);
  const [showChart, setShowChart] = useState(true);

  return (
    <div className="smart-table-wrap">
      {/* Table */}
      <div className="smart-table-scroll">
        <table className="smart-table">
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={i}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td key={ci}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Auto-chart if numeric data exists */}
      {series && series.length >= 2 && (
        <div className="smart-chart-area">
          <button
            className="smart-chart-toggle"
            onClick={() => setShowChart((v) => !v)}
          >
            {showChart ? "▾ Hide chart" : "▸ Show chart"}
          </button>
          {showChart && (
            <LumenChart
              type={series.length > 5 ? "line" : "bar"}
              data={series}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ─── Table parser ─────────────────────────────────────────────────────────────
function isTableSeparator(line: string) {
  return /^\s*\|?[\s\-:]+(\|[\s\-:]+)*\|?\s*$/.test(line) && line.includes("-");
}

function parseTableLine(line: string): string[] {
  return line
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function AnswerContent({ content }: { content: string }) {
  const nodes: React.ReactNode[] = [];
  const lines = content.split("\n");
  let i = 0;
  let listItems: string[] = [];
  let listOrdered = false;

  const flushList = () => {
    if (!listItems.length) return;
    const Tag = listOrdered ? "ol" : "ul";
    nodes.push(
      <Tag key={`list-${nodes.length}`} className="answer-list">
        {listItems.map((item, idx) => (
          <li key={idx}>
            <InlineText text={item} />
          </li>
        ))}
      </Tag>
    );
    listItems = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    // ── Chart code block ──────────────────────────────────────────────────────
    if (line.trimStart().startsWith("```chart")) {
      flushList();
      const rawLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        rawLines.push(lines[i]);
        i++;
      }
      i++; // consume closing ```
      const block = parseChartBlock(rawLines.join("\n"));
      if (block) {
        nodes.push(
          <LumenChart
            key={`chart-${nodes.length}`}
            type={block.type}
            title={block.title}
            subtitle={block.subtitle}
            data={block.data}
          />
        );
      }
      continue;
    }

    // ── Generic code block ────────────────────────────────────────────────────
    if (line.trimStart().startsWith("```")) {
      flushList();
      const lang = line.trim().replace(/^```/, "").trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++;
      nodes.push(
        <pre key={`code-${nodes.length}`} className="answer-code">
          <code data-lang={lang || undefined}>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    // ── Markdown table ────────────────────────────────────────────────────────
    if (line.includes("|")) {
      const next = lines[i + 1] || "";
      if (isTableSeparator(next)) {
        flushList();
        const headers = parseTableLine(line);
        i += 2; // skip header + separator
        const rows: string[][] = [];
        while (i < lines.length && lines[i].includes("|")) {
          rows.push(parseTableLine(lines[i]));
          i++;
        }
        nodes.push(
          <SmartTable key={`table-${nodes.length}`} headers={headers} rows={rows} />
        );
        continue;
      }
    }

    // ── Headings ──────────────────────────────────────────────────────────────
    const headingMatch = line.match(/^(#{1,3})\s+(.+)/);
    if (headingMatch) {
      flushList();
      const level = headingMatch[1].length;
      const Tag = `h${Math.min(level + 2, 6)}` as keyof JSX.IntrinsicElements;
      nodes.push(
        <Tag key={`h-${i}`}>
          <InlineText text={headingMatch[2]} />
        </Tag>
      );
      i++;
      continue;
    }

    // ── List items ────────────────────────────────────────────────────────────
    const bulletMatch = line.match(/^\s*[-*•]\s+(.+)/);
    const numberedMatch = line.match(/^\s*\d+[.)]\s+(.+)/);
    if (bulletMatch || numberedMatch) {
      if (listItems.length && listOrdered !== !!numberedMatch) flushList();
      listOrdered = !!numberedMatch;
      listItems.push((bulletMatch ?? numberedMatch)![1]);
      i++;
      continue;
    }

    // ── Blank line ─────────────────────────────────────────────────────────────
    if (!line.trim()) {
      flushList();
      i++;
      continue;
    }

    // ── Paragraph ─────────────────────────────────────────────────────────────
    flushList();
    nodes.push(
      <p key={`p-${i}`}>
        <InlineText text={line} />
      </p>
    );
    i++;
  }

  flushList();

  return <div className="answer-content">{nodes}</div>;
}
