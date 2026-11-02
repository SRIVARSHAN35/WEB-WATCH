function BrandLogo({ symbolOnly = false, className = "" }) {
  return (
    <span className={`ww-brand ${symbolOnly ? "ww-brand-symbol-only" : ""} ${className}`.trim()}>
      <svg className="ww-brand-symbol" viewBox="0 0 48 36" fill="none" aria-hidden="true" focusable="false">
        <path className="ww-brand-eye" d="M2.5 18C8 9.5 15.2 5 24 5s16 4.5 21.5 13C40 26.5 32.8 31 24 31S8 26.5 2.5 18Z" />
        <circle className="ww-brand-globe" cx="24" cy="18" r="9" />
        <path className="ww-brand-grid" d="M15.6 15h16.8M15 18h18M15.6 21h16.8M24 9v18M20.4 9.8c-2.4 2.1-3.6 4.8-3.6 8.2s1.2 6.1 3.6 8.2m7.2-16.4c2.4 2.1 3.6 4.8 3.6 8.2s-1.2 6.1-3.6 8.2" />
        <circle className="ww-brand-spark" cx="33.7" cy="10.3" r="1.45" />
      </svg>
      {!symbolOnly && <span className="ww-wordmark"><span>Web</span><strong>Watch</strong></span>}
    </span>
  );
}

export default BrandLogo;
