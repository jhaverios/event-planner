/* Shared helpers. The signed link is the sign-in: it arrives as ?t=… once,
   is kept for the tab, and every call carries it as a bearer token. */
const JSL = (function () {
  const url = new URL(location.href);
  let token = url.searchParams.get('t') || sessionStorage.getItem('jslt') || '';
  if (url.searchParams.get('t')) {
    sessionStorage.setItem('jslt', token);
    // Keep the token out of the address bar so a shared screenshot of the
    // browser does not hand somebody else the broker's link.
    url.searchParams.delete('t');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }

  async function call(path, opts) {
    const res = await fetch(path, {
      ...opts,
      headers: { 'Content-Type': 'application/json',
                 ...(token ? { Authorization: 'Bearer ' + token } : {}),
                 ...(opts && opts.headers) }
    });
    let body = null;
    try { body = await res.json(); } catch (e) { /* non-JSON error page */ }
    if (!res.ok) {
      throw new Error((body && body.error) ||
        (res.status === 401 ? 'This link has expired. Ask for a fresh one.'
                            : 'Something went wrong (' + res.status + ')'));
    }
    return body;
  }

  return {
    get: p => call(p),
    post: (p, b) => call(p, { method: 'POST', body: JSON.stringify(b) }),
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])),
    debounce(fn, ms) {
      let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
    },
    contactLine(r) {
      const bits = [];
      if (r.phone) bits.push(r.phone);
      if (r.email) bits.push(r.email);
      return bits.length ? bits.join(' · ') : 'no contact details on file';
    },
    pct: (n, d) => d ? Math.round(100 * n / d) : 0
  };
})();
