import { usePetStore } from '../../store/pet'

export function PetEffects() {
  const effects = usePetStore((state) => state.effects)

  if (effects.length === 0) return null

  return (
    <div className="fixed inset-0 pointer-events-none z-50 overflow-hidden">
      {effects.map((eff) => (
        <div
          key={eff.id}
          className="absolute animate-float-up text-sm font-bold drop-shadow-md select-none"
          style={{ left: eff.x, top: eff.y }}
        >
          {eff.type === 'heart' && (
            <span className="text-pink-400 text-lg flex items-center gap-0.5">
              💖 <span className="text-xs">{eff.text || '+亲密'}</span>
            </span>
          )}
          {eff.type === 'crumbs' && (
            <span className="text-amber-300 text-xs bg-slate-900/80 px-2 py-0.5 rounded-full border border-amber-500/40">
              ✨ {eff.text || '+饱食'}
            </span>
          )}
        </div>
      ))}
    </div>
  )
}
