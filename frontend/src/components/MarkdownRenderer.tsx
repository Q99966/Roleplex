import React, { useState, useCallback, useMemo } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Copy, Check, ExternalLink } from 'lucide-react'

interface MarkdownRendererProps {
  /** 待渲染的 Markdown 原文字符串 */
  content: string
  /** 当前消息是否正处于流式生成状态 */
  isGenerating?: boolean
  /** 自定义外层容器样式类名 */
  className?: string
}

/**
 * 安全复制文本到剪贴板，带有 DOM 降级兜底。
 * @param text 需要复制的纯文本。
 * @returns 复制成功返回 true，否则返回 false。
 */
async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // 剪贴板 API 不可用或权限拒绝时走下方降级方案
    }
  }
  if (typeof document === 'undefined') return false
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.opacity = '0'
    document.body.appendChild(textArea)
    textArea.select()
    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)
    return successful
  } catch {
    return false
  }
}

/**
 * 校验链接安全性，防御 javascript: 及恶意伪协议 XSS。
 * @param href 原始链接地址。
 * @returns 安全的链接地址或安全降级占位。
 */
function sanitizeHref(href?: string): string {
  return defaultUrlTransform(href ?? '') || '#'
}

/** 代码块组件：展示语言标签、一键复制按钮与横向滚动代码区。 */
function CodeBlock({ children }: { children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false)

  // 从 AST 子节点中解析语言类型与纯文本代码
  const { codeString, language } = useMemo(() => {
    let rawCode = ''
    let lang = ''

    if (React.isValidElement(children)) {
      const childProps = children.props as { className?: string; children?: React.ReactNode }
      const match = /language-([a-zA-Z0-9_-]+)/.exec(childProps.className || '')
      if (match) lang = match[1]
      rawCode = String(childProps.children ?? '')
    } else {
      rawCode = String(children ?? '')
    }

    return {
      codeString: rawCode.replace(/\n$/, ''),
      language: lang || 'code',
    }
  }, [children])

  const handleCopy = useCallback(async () => {
    if (!codeString) return
    const success = await copyTextToClipboard(codeString)
    if (success) {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }, [codeString])

  return (
    <div className="my-2.5 overflow-hidden rounded-xl border border-slate-800 bg-slate-950/90 shadow-inner">
      <div className="flex items-center justify-between border-b border-slate-800/80 bg-slate-900/90 px-3.5 py-1.5 text-[11px] text-slate-400 select-none">
        <span className="font-mono font-medium text-indigo-300/90 lowercase">{language}</span>
        <button
          type="button"
          onClick={() => void handleCopy()}
          aria-label={copied ? '已复制' : '复制代码'}
          className="flex items-center gap-1.5 rounded px-2 py-0.5 text-[10px] text-slate-400 hover:bg-slate-800 hover:text-slate-200 transition-colors"
        >
          {copied ? (
            <>
              <Check size={12} className="text-emerald-400" />
              <span className="text-emerald-400">已复制</span>
            </>
          ) : (
            <>
              <Copy size={12} />
              <span>复制</span>
            </>
          )}
        </button>
      </div>
      <pre className="overflow-x-auto p-3.5 text-xs font-mono text-slate-200 leading-relaxed">
        <code>{codeString}</code>
      </pre>
    </div>
  )
}

/**
 * Markdown 富文本渲染组件。
 * 针对 Roleplex 深色主题调优排版与代码高亮质感，支持 GFM（表格、删除线、任务列表、自动链接）与流式渐进展示。
 *
 * @param props 组件入参，包含 markdown 内容与生成中状态。
 */
export function MarkdownRenderer({ content, isGenerating = false, className = '' }: MarkdownRendererProps) {
  const customComponents = useMemo(() => ({
    // 标题系统：层级清晰且第一行无多余上边距
    h1: ({ children }: { children?: React.ReactNode }) => (
      <h3 className="mt-3.5 mb-2 text-base font-bold text-white border-b border-slate-800/80 pb-1.5 first:mt-0 tracking-tight">
        {children}
      </h3>
    ),
    h2: ({ children }: { children?: React.ReactNode }) => (
      <h4 className="mt-3 mb-1.5 text-sm font-bold text-white border-b border-slate-800/50 pb-1 first:mt-0 tracking-tight">
        {children}
      </h4>
    ),
    h3: ({ children }: { children?: React.ReactNode }) => (
      <h5 className="mt-2.5 mb-1 text-xs font-bold text-slate-100 first:mt-0 tracking-tight">
        {children}
      </h5>
    ),
    h4: ({ children }: { children?: React.ReactNode }) => (
      <h6 className="mt-2 mb-1 text-xs font-semibold text-slate-200 first:mt-0">
        {children}
      </h6>
    ),
    h5: ({ children }: { children?: React.ReactNode }) => (
      <div role="heading" aria-level={6} className="mt-1.5 mb-0.5 text-xs font-medium text-slate-300 first:mt-0">
        {children}
      </div>
    ),
    h6: ({ children }: { children?: React.ReactNode }) => (
      <div role="heading" aria-level={6} className="mt-1.5 mb-0.5 text-xs font-medium text-slate-400 first:mt-0">
        {children}
      </div>
    ),

    // 段落
    p: ({ children }: { children?: React.ReactNode }) => (
      <p className="mb-2 last:mb-0 leading-relaxed text-sm text-slate-200">
        {children}
      </p>
    ),

    // 强调与装饰
    strong: ({ children }: { children?: React.ReactNode }) => (
      <strong className="font-semibold text-white">{children}</strong>
    ),
    em: ({ children }: { children?: React.ReactNode }) => (
      <em className="italic text-slate-200">{children}</em>
    ),
    del: ({ children }: { children?: React.ReactNode }) => (
      <del className="line-through text-slate-500">{children}</del>
    ),

    // 列表系统
    ul: ({ children }: { children?: React.ReactNode }) => (
      <ul className="my-2 ml-4 list-disc list-outside space-y-1 text-sm text-slate-200 last:mb-0">
        {children}
      </ul>
    ),
    ol: ({ children }: { children?: React.ReactNode }) => (
      <ol className="my-2 ml-4 list-decimal list-outside space-y-1 text-sm text-slate-200 last:mb-0">
        {children}
      </ol>
    ),
    li: ({ children }: { children?: React.ReactNode }) => (
      <li className="leading-relaxed pl-1">{children}</li>
    ),

    // 引用块
    blockquote: ({ children }: { children?: React.ReactNode }) => (
      <blockquote className="my-2.5 border-l-2 border-indigo-500/70 bg-indigo-950/20 px-3.5 py-1.5 rounded-r-lg text-xs text-slate-300 italic">
        {children}
      </blockquote>
    ),

    // 分割线
    hr: () => <hr className="my-3 border-t border-slate-800" />,

    // 行内代码与代码块
    pre: CodeBlock,
    code: ({ children, className: codeClass }: { children?: React.ReactNode; className?: string }) => (
      <code className={`rounded-md bg-slate-800/80 px-1.5 py-0.5 text-xs font-mono text-indigo-300 border border-slate-700/50 ${codeClass || ''}`}>
        {children}
      </code>
    ),

    // 表格系统
    table: ({ children }: { children?: React.ReactNode }) => (
      <div className="my-2.5 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950/40">
        <table className="w-full text-left text-xs border-collapse">{children}</table>
      </div>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => (
      <thead className="bg-slate-800/70 border-b border-slate-800 text-slate-200 font-semibold">{children}</thead>
    ),
    tbody: ({ children }: { children?: React.ReactNode }) => (
      <tbody className="divide-y divide-slate-800/60 text-slate-300">{children}</tbody>
    ),
    tr: ({ children }: { children?: React.ReactNode }) => (
      <tr className="hover:bg-slate-900/40 transition-colors">{children}</tr>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
      <th className="px-3.5 py-2 border-r border-slate-800 font-medium text-slate-200 last:border-r-0">{children}</th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
      <td className="px-3.5 py-2 border-r border-slate-800/60 text-slate-300 last:border-r-0">{children}</td>
    ),

    // 安全外部链接
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => {
      const safeHref = sanitizeHref(href)
      const isExternal = safeHref.startsWith('http://') || safeHref.startsWith('https://')
      return (
        <a
          href={safeHref}
          target={isExternal ? '_blank' : undefined}
          rel={isExternal ? 'noopener noreferrer' : undefined}
          className="inline-flex items-center gap-0.5 text-indigo-400 underline underline-offset-2 hover:text-indigo-300 transition-colors"
        >
          <span>{children}</span>
          {isExternal && <ExternalLink size={10} className="shrink-0 opacity-70" />}
        </a>
      )
    },

    // GFM 任务列表复选框
    input: ({ type, checked, disabled }: { type?: string; checked?: boolean; disabled?: boolean }) => {
      if (type === 'checkbox') {
        return (
          <input
            type="checkbox"
            checked={checked}
            disabled={disabled}
            className="mr-1.5 h-3.5 w-3.5 rounded border-slate-700 bg-slate-900 text-indigo-600 accent-indigo-500 align-middle"
            readOnly
          />
        )
      }
      return null
    },
  }), [])

  if (!content.trim()) {
    if (isGenerating) {
      return (
        <div className="flex items-center gap-1 text-indigo-400 py-0.5">
          <span className="inline-block h-2 w-2 rounded-full bg-indigo-400 animate-pulse motion-reduce:animate-none" />
          <span className="inline-block h-2 w-2 rounded-full bg-indigo-400 animate-pulse [animation-delay:200ms] motion-reduce:animate-none" />
          <span className="inline-block h-2 w-2 rounded-full bg-indigo-400 animate-pulse [animation-delay:400ms] motion-reduce:animate-none" />
        </div>
      )
    }
    return null
  }

  return (
    <div className={`markdown-body text-sm ${className}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={customComponents}>
        {content}
      </ReactMarkdown>
      {isGenerating && (
        <span className="inline-block h-3.5 w-1.5 bg-indigo-400 ml-0.5 align-middle animate-pulse motion-reduce:animate-none" />
      )}
    </div>
  )
}
