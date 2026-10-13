import { useEffect, useRef, useState } from "react";

export interface Polled<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Load once when `key` changes (null disables it) and refresh every `intervalMs`.
 * The previous data is dropped when the key changes, kept while a refresh is in flight,
 * and a failed refresh keeps showing the last good data next to the error.
 */
export function usePolled<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): Polled<T> {
  const [state, setState] = useState<Polled<T>>({ data: null, error: null, loading: key !== null });
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (key === null) { setState({ data: null, error: null, loading: false }); return; }
    setState({ data: null, error: null, loading: true });
    let alive = true;
    let controller = new AbortController();
    const run = async () => {
      controller.abort();
      controller = new AbortController();
      try {
        const data = await loadRef.current(controller.signal);
        if (alive) setState({ data, error: null, loading: false });
      } catch (error) {
        if (!alive || controller.signal.aborted) return;
        setState((previous) => ({ data: previous.data, error: error as Error, loading: false }));
      }
    };
    void run();
    const timer = window.setInterval(() => { if (!document.hidden) void run(); }, intervalMs);
    return () => { alive = false; controller.abort(); window.clearInterval(timer); };
  }, [key, intervalMs]);

  return state;
}
