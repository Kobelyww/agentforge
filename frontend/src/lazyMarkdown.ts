import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 集中管理 markdown 渲染依赖，供聊天消息与最终报告复用
export { Markdown, remarkGfm };
