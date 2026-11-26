import type { LayersResponse } from "./types";

export type PeriodKind = "scene" | "season";

export interface ParsedPeriodItem {
    period: string;
    kind: PeriodKind;
    year: string;
    month: string;
    dayOrSpan: string;
    label: string;
}

const TH_MONTHS: Record<string, string> = {
    "01": "ม.ค.",
    "02": "ก.พ.",
    "03": "มี.ค.",
    "04": "เม.ย.",
    "05": "พ.ค.",
    "06": "มิ.ย.",
    "07": "ก.ค.",
    "08": "ส.ค.",
    "09": "ก.ย.",
    "10": "ต.ค.",
    "11": "พ.ย.",
    "12": "ธ.ค.",
};

export function parsePeriodString(period: string, idx = 0): ParsedPeriodItem {
    const trimmed = period.trim();

    // รองรับรูปแบบ scene_2026-11-10 หรือ scene_2026_11_10
    const sceneMatch = trimmed.match(/^scene[_-](\d{4})[-_](\d{2})[-_](\d{2})$/i);
    if (sceneMatch) {
        const [, year, month, day] = sceneMatch;
        return {
            period: trimmed,
            kind: "scene",
            year,
            month,
            dayOrSpan: day,
            label: `วันที่ ${day} (${year}-${month}-${day}) · ${trimmed}`,
        };
    }

    // รองรับรูปแบบ season_2026_03-05 หรือ season_2026-03-05
    const seasonMatch = trimmed.match(/^season[_-](\d{4})[-_](\d{2})[-_](.+)$/i);
    if (seasonMatch) {
        const [, year, month, rest] = seasonMatch;
        const span = rest.replace(/_/g, "-");
        return {
            period: trimmed,
            kind: "season",
            year,
            month,
            dayOrSpan: span,
            label: `ช่วง ${month}-${span} · ${trimmed}`,
        };
    }

    // Fallback กรณีชื่อ period มาในรูปแบบอื่น
    const yearMatch = trimmed.match(/\b(20\d{2})\b/);
    const monthMatch = trimmed.match(/(?:^|[-_])(0[1-9]|1[0-2])(?:[-_]|$)/);
    const kind: PeriodKind = /season/i.test(trimmed) ? "season" : "scene";
    const year = yearMatch ? yearMatch[1] : "2026";
    const month = monthMatch ? monthMatch[1] : "01";

    return {
        period: trimmed,
        kind,
        year,
        month,
        dayOrSpan: trimmed || `item-${idx + 1}`,
        label: trimmed || `ช่วงที่ ${idx + 1}`,
    };
}

export function buildPeriodItems(
    periods: LayersResponse["periods"] | string[] | undefined | null,
    fallbackCurrent?: string | null
): ParsedPeriodItem[] {
    const rawList: string[] = [];
    if (Array.isArray(periods)) {
        for (const item of periods) {
            if (typeof item === "string") rawList.push(item);
            else if (item && typeof item.period === "string") rawList.push(item.period);
        }
    }
    if (fallbackCurrent && !rawList.includes(fallbackCurrent)) {
        rawList.unshift(fallbackCurrent);
    }

    const seen = new Set<string>();
    const parsed: ParsedPeriodItem[] = [];
    rawList.forEach((p, i) => {
        if (!p || seen.has(p)) return;
        seen.add(p);
        parsed.push(parsePeriodString(p, i));
    });
    return parsed;
}

export class CascadingPeriodController {
    private items: ParsedPeriodItem[] = [];
    private currentPeriod: string | null = null;
    private onPeriodChange?: (period: string) => void;

    private kindEl: HTMLSelectElement | null;
    private yearEl: HTMLSelectElement | null;
    private monthEl: HTMLSelectElement | null;
    private dayEl: HTMLSelectElement | null;

    constructor(onPeriodChange?: (period: string) => void) {
        this.onPeriodChange = onPeriodChange;
        this.kindEl = document.getElementById("period-kind") as HTMLSelectElement | null;
        this.yearEl = document.getElementById("period-year") as HTMLSelectElement | null;
        this.monthEl = document.getElementById("period-month") as HTMLSelectElement | null;
        this.dayEl = document.getElementById("period-day") as HTMLSelectElement | null;
        this.bindEvents();
    }

    private bindEvents() {
        if (this.kindEl) {
            this.kindEl.onchange = () => {
                this.rebuildYears(undefined, true);
            };
        }
        if (this.yearEl) {
            this.yearEl.onchange = () => {
                this.rebuildMonths(undefined, true);
            };
        }
        if (this.monthEl) {
            this.monthEl.onchange = () => {
                this.rebuildDays(undefined, true);
            };
        }
        if (this.dayEl) {
            this.dayEl.onchange = () => {
                const chosen = this.dayEl?.value;
                if (chosen && chosen !== this.currentPeriod) {
                    this.currentPeriod = chosen;
                    this.onPeriodChange?.(chosen);
                }
            };
        }
    }

    getPeriod(): string | null {
        return this.currentPeriod;
    }

    setPeriods(
        periods: LayersResponse["periods"] | string[] | undefined | null,
        selectedPeriod?: string | null,
        notify = false
    ) {
        const merged = buildPeriodItems(periods, selectedPeriod || this.currentPeriod);
        if (merged.length > 0) {
            this.items = merged;
        }

        const target =
            (selectedPeriod && this.items.some((x) => x.period === selectedPeriod)
                ? selectedPeriod
                : null) ||
            (this.currentPeriod && this.items.some((x) => x.period === this.currentPeriod)
                ? this.currentPeriod
                : null) ||
            this.items[0]?.period ||
            null;

        this.currentPeriod = target;
        const targetItem = this.items.find((x) => x.period === target) || this.items[0];
        if (!targetItem) return;

        this.syncSelectsToItem(targetItem);
        if (notify && target) {
            this.onPeriodChange?.(target);
        }
    }

    private syncSelectsToItem(item: ParsedPeriodItem) {
        if (this.kindEl) {
            const availableKinds = Array.from(new Set(this.items.map((x) => x.kind)));
            const kindsToShow: PeriodKind[] = availableKinds.length
                ? availableKinds
                : ["scene", "season"];
            this.kindEl.innerHTML = kindsToShow
                .map(
                    (k) =>
                        `<option value="${k}">${k === "scene" ? "scene (รายวัน/รายฉาก)" : "season (รายฤดูกาล)"
                        }</option>`
                )
                .join("");
            this.kindEl.value = item.kind;
        }
        this.rebuildYears(item.year, false, item.month, item.period);
    }

    private rebuildYears(
        preferredYear?: string,
        notify = false,
        preferredMonth?: string,
        preferredPeriod?: string
    ) {
        if (!this.yearEl) return;
        const kind = (this.kindEl?.value as PeriodKind) || "scene";
        const byKind = this.items.filter((x) => x.kind === kind);
        const years = Array.from(new Set(byKind.map((x) => x.year))).sort((a, b) =>
            b.localeCompare(a)
        );

        if (!years.length) {
            this.yearEl.innerHTML = `<option value="">—</option>`;
            this.rebuildMonths(undefined, notify);
            return;
        }

        this.yearEl.innerHTML = years
            .map((y) => `<option value="${y}">${y}</option>`)
            .join("");
        const chosenYear =
            preferredYear && years.includes(preferredYear) ? preferredYear : years[0];
        this.yearEl.value = chosenYear;

        this.rebuildMonths(preferredMonth, notify, preferredPeriod);
    }

    private rebuildMonths(
        preferredMonth?: string,
        notify = false,
        preferredPeriod?: string
    ) {
        if (!this.monthEl) return;
        const kind = (this.kindEl?.value as PeriodKind) || "scene";
        const year = this.yearEl?.value || "";
        const byYear = this.items.filter((x) => x.kind === kind && x.year === year);
        const months = Array.from(new Set(byYear.map((x) => x.month))).sort((a, b) =>
            b.localeCompare(a)
        );

        if (!months.length) {
            this.monthEl.innerHTML = `<option value="">—</option>`;
            this.rebuildDays(undefined, notify);
            return;
        }

        this.monthEl.innerHTML = months
            .map((m) => {
                const th = TH_MONTHS[m] ? ` (${TH_MONTHS[m]})` : "";
                return `<option value="${m}">${m}${th}</option>`;
            })
            .join("");
        const chosenMonth =
            preferredMonth && months.includes(preferredMonth) ? preferredMonth : months[0];
        this.monthEl.value = chosenMonth;

        this.rebuildDays(preferredPeriod, notify);
    }

    private rebuildDays(preferredPeriod?: string, notify = false) {
        if (!this.dayEl) return;
        const kind = (this.kindEl?.value as PeriodKind) || "scene";
        const year = this.yearEl?.value || "";
        const month = this.monthEl?.value || "";

        const matches = this.items.filter(
            (x) => x.kind === kind && x.year === year && x.month === month
        );

        if (!matches.length) {
            this.dayEl.innerHTML = `<option value="">ไม่มีข้อมูล</option>`;
            return;
        }

        this.dayEl.innerHTML = matches
            .map((m) => `<option value="${m.period}">${m.label}</option>`)
            .join("");

        const chosen =
            preferredPeriod && matches.some((m) => m.period === preferredPeriod)
                ? preferredPeriod
                : matches[0].period;
        this.dayEl.value = chosen;

        const changed = chosen !== this.currentPeriod;
        this.currentPeriod = chosen;
        if (notify && changed) {
            this.onPeriodChange?.(chosen);
        }
    }
}