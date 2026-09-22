import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/** 原生模态层使其余页面不可交互，并在关闭后归还焦点；内容来自既有 Owner 接口。 */
export function WorkflowDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const dialog = ref.current!, previous = document.activeElement as HTMLElement | null
    dialog.showModal()
    return () => { dialog.close(); if (previous?.isConnected) previous.focus() }
  }, [])
  return createPortal(<dialog ref={ref} aria-label={title} className="workflow-dialog" onCancel={event => { event.preventDefault(); onClose() }}
    onKeyDown={event => event.stopPropagation()}
    onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="workflow-dialog-content">{children}</div>
  </dialog>, document.body)
}
