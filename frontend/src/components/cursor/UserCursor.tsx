import {
  motion,
  useMotionValue,
  useMotionTemplate,
  useReducedMotion,
  useSpring,
} from 'motion/react'
import { useEffect, useState } from 'react'

const SIZE = 21
const MAX_TILT = 14
const PRESS_SCALE = 0.81
const ACCENT = '#0ad0f3'

const POSITION_SPRING = { stiffness: 380, damping: 32, mass: 0.6 }
const TILT_SPRING = { stiffness: 220, damping: 26, mass: 0.7 }

/** Dashboard-wide replacement for the OS cursor: an arrow that eases toward
    the pointer instead of snapping to it, and leans into the direction of
    travel. Purely decorative, so it's gated off wherever there's no "hover"
    to trigger it in the first place -- reduced motion, and touch/coarse
    pointers that have no real cursor to replace -- rather than degraded. */
export function UserCursor() {
  const reduceMotion = useReducedMotion()
  const [active, setActive] = useState(false)

  const rawX = useMotionValue(0)
  const rawY = useMotionValue(0)
  const rawTilt = useMotionValue(0)
  const rawScale = useMotionValue(1)

  const x = useSpring(rawX, POSITION_SPRING)
  const y = useSpring(rawY, POSITION_SPRING)
  const tilt = useSpring(rawTilt, TILT_SPRING)
  const scale = useSpring(rawScale, POSITION_SPRING)

  const transform = useMotionTemplate`translate(-50%, -50%) translate(${x}px, ${y}px) rotate(${tilt}deg) scale(${scale})`

  useEffect(() => {
    if (reduceMotion) return
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return

    let lastX = 0

    const onMove = (e: PointerEvent) => {
      const dx = e.clientX - lastX
      lastX = e.clientX
      rawX.set(e.clientX)
      rawY.set(e.clientY)
      if (Math.abs(dx) > 0.5) {
        rawTilt.set(Math.max(-MAX_TILT, Math.min(MAX_TILT, dx * 1.5)))
      }
    }
    const onDown = () => rawScale.set(PRESS_SCALE)
    const onUp = () => rawScale.set(1)
    const onEnter = () => setActive(true)
    const onLeave = () => setActive(false)

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerdown', onDown)
    window.addEventListener('pointerup', onUp)
    document.documentElement.addEventListener('mouseenter', onEnter)
    document.documentElement.addEventListener('mouseleave', onLeave)
    document.documentElement.classList.add('user-cursor-active')

    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointerup', onUp)
      document.documentElement.removeEventListener('mouseenter', onEnter)
      document.documentElement.removeEventListener('mouseleave', onLeave)
      document.documentElement.classList.remove('user-cursor-active')
    }
  }, [reduceMotion, rawX, rawY, rawTilt, rawScale])

  if (reduceMotion) return null

  return (
    <motion.div
      aria-hidden
      className="user-cursor"
      style={{ transform, opacity: active ? 1 : 0 }}
    >
      <svg width={SIZE} height={SIZE} viewBox="0 0 24 24" fill="none">
        <path d="M4 2.5L20.5 12L12.7 13.6L9 21.5L4 2.5Z" fill={ACCENT} />
      </svg>
    </motion.div>
  )
}
