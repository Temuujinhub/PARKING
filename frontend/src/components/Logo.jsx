// Easy Parking брэнд лого (brandbook: ногоон E + саарал зам P)
export function LogoMark({ size = 34 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 52 60" fill="none" aria-hidden="true">
      {/* E — ногоон */}
      <path d="M38 9 H15 Q9 9 9 15 V27 Q9 33 15 33 H27" stroke="#4CAF52" strokeWidth="9" strokeLinecap="round" fill="none" />
      <path d="M15 21 H25" stroke="#4CAF52" strokeWidth="9" strokeLinecap="round" />
      {/* P — саарал зам */}
      <path d="M25 21 H37 Q44 21 44 28 V30 Q44 37 37 37 H28 V53" stroke="#585856" strokeWidth="9" strokeLinecap="round" fill="none" />
      {/* Замын тасархай шугам */}
      <path d="M25 21 H37 Q44 21 44 28 V30 Q44 37 37 37 H28 V53" stroke="#fff" strokeWidth="1.6" strokeDasharray="4 5" strokeLinecap="round" fill="none" />
    </svg>
  )
}

export function LogoText({ className = 'text-lg' }) {
  return (
    <span className={`font-black tracking-tight ${className}`}>
      <span className="text-accent">EASY</span>{' '}
      <span className="text-slate-300">PARKING</span>
    </span>
  )
}

export default function Logo({ size = 34, textClass }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <LogoMark size={size} />
      <LogoText className={textClass} />
    </span>
  )
}
