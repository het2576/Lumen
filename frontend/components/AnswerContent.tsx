"use client";

import { Fragment } from "react";
import LumenChart from "./LumenChart";

function InlineText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|\[\d+(?:,\s*\d+)*\])/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        if (/^\[\d/.test(part)) {
          return <sup key={i} className="inline-cite">{part}</sup>;
        }
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

function splitCodeBlocks(content: string): Array<{ type: "text" | "chart" | "code"; raw: string; lang?: string }> {
  const segments: Array<{ type: "text" | "chart" | "code"; raw: string; lang?: string }> = [];
  const fenceRe = /^```(\w*)\n([\s\S]*?)^```/gm;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = fenceRe.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: "text", raw: content.slice(lastIndex, match.index) });
    }
    const lang = match[1].toLowerCase();
    const body = match[2];
    if (lang === "chart") {
      segments.push({ type: "chart", raw: body });
    } else {
      segments.push({ type: "code", raw: body, lang });
    }
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < content.length) {
    segments.push({ type: "text", raw: content.slice(lastIndex) });
  }

  return segments;
}

function parseMarkdownLines(text: string): React.ReactNode[] {
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  let list: string[] = [];
  let table: string[][] = [];

  const flushList = () => {
    if (list.length) {
      nodes.push(
        <ul key={`list-${nodes.length}`} className="answer-list">
          {list.map((item, i) => <li key={i}><InlineText text={item} /></li>)}
        </ul>
      );
      list = [];
    }
  };

  const flushTable = () => {
    if (table.length >= 2) {
      const [headers, divider, ...rows] = table;
      if (divider && divider.every((cell) => /^:?-{3,}:?$/.test(cell.trim()))) {
        nodes.push(
          <div className="answer-table-wrap" key={`table-${nodes.length}`}>
            <table className="answer-table">
              <thead><tr>{headers.map((cell, i) => <th key={i}><InlineText text={cell} /></th>)}</tr></thead>
              <tbody>{rows.map((row, ri) => <tr key={ri}>{headers.map((_, ci) => <td key={ci}><InlineText text={row[ci] ?? ""} /></td>)}</tr>)}</tbody>
            </table>
          </div>
        );
      } else {
        table.forEach((row, ri) => nodes.push(<p key={`tl-${ri}`}><InlineText text={row.join(" | ")} /></p>));
      }
    }
    table = [];
  };

  lines.forEach((line, index) => {
    if (/^\s*\|.+\|\s*$/.test(line)) {
      flushList();
      table.push(line.trim().slice(1, -1).split("|").map((c) => c.trim()));
      return;
    }
    flushTable();
    const bullet = line.match(/^\s*[-*•]\s+(.+)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)/);
    if (bullet || numbered) { list.push((bullet ?? numbered)![1]); return; }
    flushList();
    if (!line.trim()) return;
    const heading = line.match(/^#{1,3}\s+(.+)/);
    if (heading) { nodes.push(<h3 key={`h-${index}`}><InlineText text={heading[1]} /></h3>); return; }
    nodes.push(<p key={`p-${index}`}><InlineText text={line} /></p>);
  });

  flushList();
  flushTable();
  return nodes;
}

export default function AnswerContent({ content }: { content: string }) {
  const segments = splitCodeBlocks(content);

  return (
    <div className="answer-content">
      {segments.map((seg, i) => {
        if (seg.type === "chart") {
          try {
            const parsed = JSON.parse(seg.raw.trim());
            if (parsed && Array.isArray(parsed.data) && parsed.data.length > 0) {
              return (
                <LumenChart
                  key={`chart-${i}`}
                  type={parsed.type ?? "bar"}
                  title={parsed.title}
                  subtitle={parsed.subtitle}
                  data={parsed.data}
                />
              );
            }
          } catch {
            // malformed JSON → show as code block
          }
          return <pre key={`chart-err-${i}`} className="answer-code"><code>{seg.raw}</code></pre>;
        }

        if (seg.type === "code") {
          return <pre key={`code-${i}`} className="answer-code"><code>{seg.raw}</code></pre>;
        }

        return <Fragment key={`text-${i}`}>{parseMarkdownLines(seg.raw)}</Fragment>;
      })}
    </div>
  );
}
