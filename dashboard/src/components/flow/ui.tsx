// Small building blocks shared by the new-video flow pages.
import type { ReactNode } from 'react'

export function PageTitle({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="flex flex-col gap-1 mb-5">
      <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>{title}</h1>
      {subtitle && <p className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>{subtitle}</p>}
    </div>
  )
}

export function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border p-4 ${className}`} style={{ background: 'var(--card)', borderColor: 'var(--border)' }}>
      {children}
    </section>
  )
}

export function Label({ children, hint, htmlFor }: { children: ReactNode; hint?: string; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="flex flex-col gap-0.5 mb-1.5">
      <span className="text-xs font-medium" style={{ color: 'var(--text)' }}>{children}</span>
      {hint && <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{hint}</span>}
    </label>
  )
}

export function Notice({ tone, children }: { tone: 'error' | 'warn' | 'info' | 'ok'; children: ReactNode }) {
  const color = { error: 'var(--red)', warn: 'var(--yellow)', info: 'var(--accent)', ok: 'var(--green)' }[tone]
  return (
    <div className="rounded-md border px-3 py-2 text-xs leading-relaxed"
         style={{ borderColor: color, color: 'var(--text)', background: 'var(--surface)' }}
         role={tone === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}

export function Toggle({ checked, onChange, label, id }: { checked: boolean; onChange: (v: boolean) => void; label: string; id: string }) {
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm cursor-pointer select-none" style={{ color: 'var(--text)' }}>
      <input id={id} type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} className="w-4 h-4 accent-blue-500" />
      {label}
    </label>
  )
}

/** Big primary action with an optional cost line under the label. */
export function ActionButton({ children, cost, onClick, disabled, tone = 'primary', type = 'button' }: {
  children: ReactNode; cost?: string; onClick?: () => void; disabled?: boolean
  tone?: 'primary' | 'secondary' | 'danger'; type?: 'button' | 'submit'
}) {
  const bg = { primary: 'var(--accent)', secondary: 'var(--surface)', danger: 'var(--red)' }[tone]
  const fg = tone === 'secondary' ? 'var(--text)' : '#fff'
  return (
    <button type={type} onClick={onClick} disabled={disabled}
            className="inline-flex flex-col items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-opacity hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ background: bg, color: fg, border: tone === 'secondary' ? '1px solid var(--border)' : 'none' }}>
      <span>{children}</span>
      {cost && <span className="text-[11px] font-normal opacity-90">{cost}</span>}
    </button>
  )
}

/** A centered dialog over a dim backdrop. `onClose` runs on Escape or a backdrop click. */
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}
         onClick={onClose} onKeyDown={e => { if (e.key === 'Escape') onClose() }} role="presentation">
      <div role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}
           className="w-full max-w-lg rounded-lg border p-5 flex flex-col gap-3"
           style={{ background: 'var(--card)', borderColor: 'var(--border)', color: 'var(--text)' }}>
        <h2 className="text-sm font-semibold">{title}</h2>
        {children}
      </div>
    </div>
  )
}
