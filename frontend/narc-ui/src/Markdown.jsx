import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
const components = {
  a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  img: ({ alt }) => <span>{alt || "Image"}</span>,
};
export default function Markdown({ children }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>{children}</ReactMarkdown>;
}
