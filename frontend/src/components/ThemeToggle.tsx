import { IconCheck, IconMonitor, IconMoon, IconSun } from '@/components/icons'
import { useDismissableMenu } from '@/hooks/useDismissableMenu'
import type { ResolvedTheme, ThemePreference } from '@/lib/theme'

/* Built on useDismissableMenu, the same Escape-closes / click-outside-closes /
   focus-returns contract the Run menu and the assistant panel already use,
   rather than on a headless menu library. Three mutually exclusive options do
   not justify a new dependency, and staying on the house pattern keeps this
   surface out of the way of the smooth-scroll container, which a portalled
   overlay would have to negotiate with. */

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof IconSun }[] = [
  { value: 'light', label: 'Light', Icon: IconSun },
  { value: 'dark', label: 'Dark', Icon: IconMoon },
  { value: 'system', label: 'System', Icon: IconMonitor },
]

export function ThemeToggle({
  preference,
  resolved,
  onSelect,
}: {
  preference: ThemePreference
  resolved: ResolvedTheme
  onSelect: (next: ThemePreference) => void
}) {
  const { open, setOpen, close, triggerRef, panelRef } = useDismissableMenu<
    HTMLButtonElement,
    HTMLDivElement
  >()

  // The icon shows what the page currently LOOKS like; the accessible name says
  // what was actually chosen, and names the OS answer when that choice defers.
  // Collapsing the two -- a moon labelled "Dark" while the preference is
  // "System" -- is the thing most theme toggles get wrong.
  const TriggerIcon = resolved === 'light' ? IconSun : IconMoon
  const label =
    preference === 'system' ? `Theme: system (${resolved})` : `Theme: ${preference}`

  return (
    <div className="menu-wrap">
      <button
        ref={triggerRef}
        className="btn icon-btn theme-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen(!open)}
      >
        <TriggerIcon />
      </button>

      <div
        ref={panelRef}
        className="menu theme-menu"
        role="menu"
        aria-label="Theme"
        hidden={!open}
      >
        {OPTIONS.map(({ value, label: optionLabel, Icon }) => {
          const selected = preference === value
          return (
            <button
              key={value}
              type="button"
              role="menuitemradio"
              aria-checked={selected}
              className="menu-item theme-option"
              onClick={() => {
                onSelect(value)
                close()
              }}
            >
              <Icon className="theme-option-icon" />
              <span>{optionLabel}</span>
              {/* Occupies its cell whether or not it is the chosen row, so the
                  labels do not shift sideways as the selection moves. */}
              <IconCheck className={`theme-option-check${selected ? '' : ' is-hidden'}`} />
            </button>
          )
        })}
      </div>
    </div>
  )
}
