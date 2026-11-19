import { useCallback, useEffect, useState } from "react";

/** Loads something once and exposes {data, error, loading, reload}. */
export default function useQr(loader, enabled = true) {
  const [state, setState] = useState({ data: null, error: null, loading: enabled });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      setState({ data: await loader(), error: null, loading: false });
    } catch (error) {
      setState({ data: null, error, loading: false });
    }
  }, [loader]);

  useEffect(() => {
    if (enabled) load();
  }, [enabled, load]);

  return { ...state, reload: load };
}