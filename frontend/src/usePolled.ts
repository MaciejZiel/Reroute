import { useCallback, useEffect, useRef, useState } from "react";

interface PolledState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

export interface Polled<T> extends PolledState<T> {
  /** Run the load again now, for a retry button next to an error. */
  retry: () => void;
}

/**
 * Load once when `key` changes (null disables it) and refresh every `intervalMs`.
 * The previous data is dropped when the key changes, kept while a refresh is in flight,
 * and a failed refresh keeps showing the last good data next to the error.
 * Changing only the interval re-arms the timer and keeps the data.
 */
export function usePolled<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
): Polled<T> {
  const [state, setState] = useState<PolledState<T>>({ data: null, error: null, loading: key !== null });
  const loadRef = useRef(load);
  loadRef.current = load;
  const runRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    if (key === null) { setState({ data: null, error: null, loading: false }); runRef.current = () => undefined; return; }
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
    runRef.current = () => { void run(); };
    void run();
    return () => { alive = false; controller.abort(); runRef.current = () => undefined; };
  }, [key]);

  useEffect(() => {
    if (key === null) return;
    const timer = window.setInterval(() => { if (!document.hidden) runRef.current(); }, intervalMs);
    return () => window.clearInterval(timer);
  }, [key, intervalMs]);

  const retry = useCallback(() => { runRef.current(); }, []);
  return { ...state, retry };
}
