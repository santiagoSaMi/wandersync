import { useEffect, useRef } from "react";

// <dialog> nativo: foco atrapado, Escape para cerrar y fondo inerte, sin librerías.
export default function Dialog({ title, onClose, children, wide = false }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
  }, []);

  return (
    <dialog
      ref={ref}
      className={wide ? "dialog dialog-wide" : "dialog"}
      onClose={onClose}
      aria-labelledby="dialog-title"
    >
      <div className="dialog-head">
        <h2 id="dialog-title">{title}</h2>
        <button type="button" className="icon-btn" aria-label="Cerrar" onClick={() => ref.current?.close()}>
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
            <path d="M4 4l12 12M16 4L4 16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </div>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}
