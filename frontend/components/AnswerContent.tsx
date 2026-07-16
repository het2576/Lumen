import { Fragment } from "react";

function InlineText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={index}>{part.slice(2, -2)}</strong> : <Fragment key={index}>{part}</Fragment>)}</>;
}

export default function AnswerContent({ content }: { content: string }) {
  const lines = content.split("\n");
  const nodes: React.ReactNode[] = [];
  let list: string[] = [];
  const flushList = () => { if (list.length) { nodes.push(<ul key={`list-${nodes.length}`} className="answer-list">{list.map((item, index) => <li key={index}><InlineText text={item}/></li>)}</ul>); list = []; } };

  lines.forEach((line, index) => {
    const bullet = line.match(/^\s*[-*•]\s+(.+)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)/);
    if (bullet || numbered) { list.push((bullet ?? numbered)?.[1] ?? line); return; }
    flushList();
    if (!line.trim()) return;
    const heading = line.match(/^#{1,3}\s+(.+)/);
    if (heading) { nodes.push(<h3 key={`heading-${index}`}><InlineText text={heading[1]}/></h3>); return; }
    nodes.push(<p key={`paragraph-${index}`}><InlineText text={line}/></p>);
  });
  flushList();
  return <div className="answer-content">{nodes}</div>;
}
