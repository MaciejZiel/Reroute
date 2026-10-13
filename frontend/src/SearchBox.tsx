import { useEffect, useId, useRef, useState } from "react";
import { searchStops } from "./api";
import type { TranslationKey } from "./i18n";
import type { StopMatch } from "./types";

type T = (key: TranslationKey) => string;

export default function SearchBox({ t, onPick }: { t: T; onPick: (match: StopMatch) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StopMatch[] | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const text = query.trim();
    if (!text) { setResults(null); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchStops(text, controller.signal)
        .then((response) => { setResults(response.results); setActive(0); })
        .catch(() => { if (!controller.signal.aborted) setResults([]); });
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query]);

  useEffect(() => {
    const close = (event: PointerEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, []);

  const pick = (match: StopMatch) => { setQuery(""); setResults(null); setOpen(false); onPick(match); };
  const showList = open && results !== null;

  return (
    <div className="search-box" ref={box}>
      <label className="section-kicker" htmlFor={`${listId}-input`}>{t("searchLabel")}</label>
      <input
        id={`${listId}-input`}
        type="search"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={showList && results?.length ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder={t("searchPlaceholder")}
        value={query}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === "Escape") { setOpen(false); return; }
          if (!results?.length) return;
          if (event.key === "ArrowDown") { event.preventDefault(); setActive((active + 1) % results.length); }
          if (event.key === "ArrowUp") { event.preventDefault(); setActive((active - 1 + results.length) % results.length); }
          if (event.key === "Enter") { event.preventDefault(); pick(results[active]); }
        }}
      />
      {showList && (
        <ul className="search-results" id={listId} role="listbox">
          {results.length === 0 && <li className="search-empty">{t("searchNoResults")}</li>}
          {results.map((match, index) => (
            <li key={match.id} id={`${listId}-${index}`} role="option" aria-selected={index === active}
              className={index === active ? "active" : ""}
              onPointerDown={(event) => { event.preventDefault(); pick(match); }}
              onPointerEnter={() => setActive(index)}>
              <strong>{match.name}</strong>
              <small>{match.lines.slice(0, 8).join(" · ")}{match.lines.length > 8 ? " …" : ""}</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
