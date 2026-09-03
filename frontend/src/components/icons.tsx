/* The dashboard's own icon set, carried over from the vanilla markup rather
   than swapped for lucide: these are drawn on a 20-unit grid at the stroke
   weight the rail was spaced for, and the tab icons in particular (the
   ledger-gap triangle, the clock-with-a-handle activity mark) are specific to
   what this app does. Colour always comes from currentColor. */

type IconProps = { className?: string }

export const IconOverview = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M3 9.5L10 3l7 6.5M5 8.5V17h10V8.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

export const IconExceptions = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M10 3.5L17.5 16h-15L10 3.5z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    <path d="M10 8.5v3.2M10 14v.01" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

export const IconInsights = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M3.5 16.5v-5M8.5 16.5v-9M13.5 16.5v-13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
)

export const IconAudit = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <circle cx="10" cy="10.5" r="7" stroke="currentColor" strokeWidth="1.6" />
    <path d="M10 6.5V10.5L12.6 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M7 2.5h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

export const IconData = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <ellipse cx="10" cy="5" rx="6" ry="2.4" stroke="currentColor" strokeWidth="1.6" />
    <path d="M4 5v10c0 1.33 2.69 2.4 6 2.4s6-1.07 6-2.4V5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    <path d="M4 10c0 1.33 2.69 2.4 6 2.4s6-1.07 6-2.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

export const IconSettings = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <circle cx="10" cy="10" r="2.6" stroke="currentColor" strokeWidth="1.6" />
    <path d="M10 2.6v2M10 15.4v2M17.4 10h-2M4.6 10h-2M15.2 4.8l-1.4 1.4M6.2 13.8l-1.4 1.4M15.2 15.2l-1.4-1.4M6.2 6.2L4.8 4.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

export const IconSearch = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <circle cx="9" cy="9" r="5.6" stroke="currentColor" strokeWidth="1.7" />
    <path d="M13.2 13.2L17 17" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
  </svg>
)

export const IconChevron = (p: IconProps) => (
  <svg viewBox="0 0 14 14" fill="none" aria-hidden="true" {...p}>
    <path d="M3.5 5.5L7 9l3.5-3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

export const IconRefresh = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M16.5 10a6.5 6.5 0 1 1-1.9-4.6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    <path d="M16.5 3v3.5H13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

export const IconDownload = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M10 3v9M6.5 9L10 12.5 13.5 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M4 15.5h12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

export const IconClose = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
  </svg>
)

/** A headset, not a question mark: this opens a support conversation. */
export const IconHelp = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M4 12v-2a6 6 0 0 1 12 0v2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <rect x="2.5" y="11" width="3.4" height="5" rx="1.7" stroke="currentColor" strokeWidth="1.7" />
    <rect x="14.1" y="11" width="3.4" height="5" rx="1.7" stroke="currentColor" strokeWidth="1.7" />
  </svg>
)

/* Theme icons. The trigger wears whichever of sun/moon is actually resolved,
   so the rail reads as the state rather than as a generic settings affordance;
   the monitor is only ever a menu option, because "system" is a rule about how
   to choose, not a thing the page can look like. */
export const IconSun = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <circle cx="10" cy="10" r="3.6" stroke="currentColor" strokeWidth="1.6" />
    <path d="M10 2.2v1.8M10 16v1.8M17.8 10H16M4 10H2.2M15.5 4.5l-1.3 1.3M5.8 14.2l-1.3 1.3M15.5 15.5l-1.3-1.3M5.8 5.8L4.5 4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

export const IconMoon = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M16.2 12.3A6.8 6.8 0 0 1 7.7 3.8a6.9 6.9 0 1 0 8.5 8.5z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
  </svg>
)

export const IconMonitor = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <rect x="2.5" y="3.8" width="15" height="10" rx="1.6" stroke="currentColor" strokeWidth="1.6" />
    <path d="M7.5 16.8h5M10 13.8v3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
)

/** A tick for the selected row in a menu of mutually exclusive options. */
export const IconCheck = (p: IconProps) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...p}>
    <path d="M4.5 10.5l3.5 3.5 7.5-8" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

