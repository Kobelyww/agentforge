import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { DocInfo, SearchHit } from "../types";

export default function DocPanel({ onClose }: { onClose: () => void }) {
  const [docs, setDocs] = useState<DocInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    api.listDocs().then(setDocs).catch((e) => setError(e.message));
  }, []);

  useEffect(refresh, [refresh]);

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      await api.uploadDoc(file);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    await api.deleteDoc(id).catch((e) => setError(e.message));
    refresh();
  };

  const search = async () => {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetch("/api/documents/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, k: 5 }),
      });
      if (!resp.ok) throw new Error(`search failed: ${resp.status}`);
      const body = await resp.json();
      setHits(body.results);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="doc-panel">
      <div className="doc-panel-header">
        <h2>📚 知识库</h2>
        <button className="btn-icon" onClick={onClose}>×</button>
      </div>
      <p className="doc-hint">
        上传 Markdown / 文本文件，Agent 即可通过 <code>rag_search</code> 工具检索（混合检索：BM25 + 向量，RRF 融合）。
      </p>

      <div
        className={`dropzone ${busy ? "busy" : ""}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files[0];
          if (file) upload(file);
        }}
      >
        {busy ? "处理中…" : "点击或拖拽文件到此处上传（.md / .txt）"}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".md,.txt,.markdown,text/plain,text/markdown"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) upload(file);
          e.target.value = "";
        }}
      />
      {error && <div className="doc-error">⚠ {error}</div>}

      <div className="doc-list">
        {docs.length === 0 && <div className="empty-hint">知识库为空，上传文档试试</div>}
        {docs.map((d) => (
          <div key={d.id} className="doc-item">
            <div className="doc-info">
              <div className="doc-name">{d.name}</div>
              <div className="doc-meta">{d.chunk_count} chunks · {(d.size / 1024).toFixed(1)} KB</div>
            </div>
            <button className="session-delete" onClick={() => remove(d.id)}>×</button>
          </div>
        ))}
      </div>

      <div className="doc-search">
        <div className="section-title">检索测试</div>
        <div className="doc-search-row">
          <input
            value={query}
            placeholder="输入查询语句…"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
          <button className="btn-primary" onClick={search} disabled={busy}>搜索</button>
        </div>
        {hits !== null && (
          <div className="doc-hits">
            {hits.length === 0 && <div className="empty-hint">无结果</div>}
            {hits.map((h) => (
              <div key={h.chunk_id} className="doc-hit">
                <div className="doc-hit-meta">
                  {h.document_name} · score {h.score.toFixed(3)}
                </div>
                <div className="doc-hit-text">{h.text.slice(0, 200)}{h.text.length > 200 ? "…" : ""}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
