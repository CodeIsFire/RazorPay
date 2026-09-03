import { useEffect, useRef, useState } from 'react'
import { useToast } from '@/components/Toast'
import { CONVERSION_PROMPT } from '@/lib/conversionPrompt'

const COPIED_MS = 2000

/** The conversion prompt, as a terminal you can copy in one click.

    A fixed-height scrolling block rather than the full 60 lines inline: the
    two upload cards are what this tab is for, and a wall of text above the
    fold would bury them. The copy button carries the whole string regardless
    of what is scrolled into view, which is the only reason the height is
    safe to cap. */
export function PromptTerminal() {
  const toast = useToast()
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(CONVERSION_PROMPT)
      setCopied(true)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), COPIED_MS)
    } catch {
      // A refused clipboard (denied permission, an insecure origin) must not
      // leave the button saying "Copied" over an empty paste buffer.
      toast('Couldn’t copy the prompt — select it and copy manually.', true)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2 className="title">Convert your own data</h2>
          <div className="sub">
            Paste this into any AI chat along with your export, and it will produce a CSV
            shaped for the templates above. It encodes the same rules the upload validates.
          </div>
        </div>
      </div>

      <div className="card-body">
        <div className="terminal">
          <div className="terminal-bar">
            {/* Top-left, where the eye lands first in a left-to-right read --
                the action matters more here than the filename beside it. */}
            <button className="terminal-copy" onClick={copy}>
              {copied ? '✓ Copied' : '⧉ Copy'}
            </button>
            <span className="terminal-name">prompt.txt</span>
          </div>
          {/* tabIndex so the box can be scrolled without a mouse; a labelled
              region so a screen reader announces what it landed in. */}
          <pre
            className="terminal-body"
            role="region"
            aria-label="Conversion prompt"
            tabIndex={0}
          >
            {CONVERSION_PROMPT}
          </pre>
        </div>
      </div>
    </section>
  )
}
