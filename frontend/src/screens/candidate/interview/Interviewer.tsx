/** The SC-A06 interviewer character (wireframe INTERVIEWER_SVG). `speaking` animates the mouth while it talks. */
export function Interviewer({ speaking }: { speaking: boolean }) {
  return (
    <svg className={`interviewer${speaking ? ' speaking' : ''}`} viewBox="0 0 240 232" role="img" aria-label="면접관 캐릭터">
      <path d="M48 232 Q50 160 120 154 Q190 160 192 232 Z" fill="#33475b" />
      <path d="M100 157 L120 196 L140 157 Z" fill="#f4f6f8" />
      <path d="M116 164 L124 164 L128 194 L120 204 L112 194 Z" fill="#1d6b57" />
      <rect x="108" y="128" width="24" height="32" rx="6" fill="#efc4a2" />
      <circle cx="72" cy="100" r="9" fill="#f3cdb0" /><circle cx="168" cy="100" r="9" fill="#f3cdb0" />
      <circle cx="120" cy="96" r="48" fill="#f6d5b9" />
      <path d="M72 94 Q68 40 120 38 Q174 40 168 94 Q162 60 120 60 Q86 60 72 94 Z" fill="#2c2420" />
      <path d="M88 82 Q100 76 110 82" stroke="#2c2420" strokeWidth="3" fill="none" strokeLinecap="round" />
      <path d="M130 82 Q140 76 152 82" stroke="#2c2420" strokeWidth="3" fill="none" strokeLinecap="round" />
      <g className="eyes"><circle cx="100" cy="99" r="4.5" fill="#2c2420" /><circle cx="140" cy="99" r="4.5" fill="#2c2420" /></g>
      <circle cx="100" cy="99" r="14" fill="none" stroke="#2c2420" strokeWidth="3" />
      <circle cx="140" cy="99" r="14" fill="none" stroke="#2c2420" strokeWidth="3" />
      <path d="M114 99 L126 99" stroke="#2c2420" strokeWidth="3" />
      <circle cx="88" cy="118" r="7" fill="#f0a48c" opacity=".45" /><circle cx="152" cy="118" r="7" fill="#f0a48c" opacity=".45" />
      <ellipse className="mouth" cx="120" cy="125" rx="10" ry="3" fill="#9b3f3f" />
      <rect x="8" y="200" width="224" height="32" rx="6" fill="#b5977a" />
      <rect x="92" y="207" width="56" height="17" rx="3" fill="#fbf7f2" />
      <text x="120" y="219.5" textAnchor="middle" fontSize="9.5" fontWeight="700" fill="#33475b" fontFamily="sans-serif">AI 면접관</text>
    </svg>
  );
}
