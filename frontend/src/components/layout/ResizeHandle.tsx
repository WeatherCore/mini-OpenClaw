import * as React from 'react'
import { cn } from '@/lib/utils'

interface Props {
  /** 宽度增量（正值=变宽，已按左/右栏方向校正） */
  onResize: (delta: number) => void
  side: 'left' | 'right'
  className?: string
}

/**
 * 拖拽调宽条。mousedown 后监听 window mousemove，每次上抛相对上次的增量。
 * 内部按 side 校正方向：左栏向右拖=变宽；右栏向左拖=变宽。
 */
export function ResizeHandle({ onResize, side, className }: Props) {
  const cb = React.useRef(onResize)
  cb.current = onResize

  const handleDown = (e: React.MouseEvent) => {
    e.preventDefault()
    let lastX = e.clientX
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const move = (ev: MouseEvent) => {
      const delta = ev.clientX - lastX
      lastX = ev.clientX
      cb.current(side === 'left' ? delta : -delta)
    }
    const up = () => {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  return (
    <div
      onMouseDown={handleDown}
      className={cn(
        'w-1 shrink-0 cursor-col-resize bg-transparent hover:bg-accent/40 transition-colors',
        className,
      )}
    />
  )
}
