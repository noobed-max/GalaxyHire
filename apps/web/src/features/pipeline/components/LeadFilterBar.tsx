import type { ReactNode } from "react";
import type { LocationSuggestion } from "../../../api/discovery";
import Icon from "../../../shared/components/Icon";
import type { ApiFetch, LeadSort, SeniorityFilter } from "../../../types";
import { LocationFilter } from "./LocationFilter";

export function LeadFilterBar({
  search, setSearch, platform, setPlatform, sort, setSort,
  seniority, setSeniority, platforms, total, shown, label, actions,
  location, setLocation, api,
  locationSuggestions,
}: {
  search: string; setSearch: (v: string) => void;
  platform: string; setPlatform: (v: string) => void;
  sort: LeadSort; setSort: (v: LeadSort) => void;
  seniority: SeniorityFilter; setSeniority: (v: SeniorityFilter) => void;
  platforms: string[]; total: number; shown: number; label: string;
  location: string; setLocation: (v: string) => void;
  locationSuggestions?: LocationSuggestion[];
  api?: ApiFetch | null;
  actions?: ReactNode;
}) {
  const hasFilters = Boolean(search || platform || location || seniority !== "all" || sort !== "recommended");
  const resetFilters = () => {
    setSearch("");
    setPlatform("");
    setSeniority("all");
    setLocation("");
    setSort("recommended");
  };

  return (
    <div className="pipeline-filterbar">
      <label className="pipeline-searchbox">
        <Icon name="search" size={14} />
        <input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search by title, company, or skill"
          aria-label={`Search ${label}`}
        />
      </label>

      <div className="pipeline-filter-fields">
        <label className="pipeline-field">
          <span>Job site</span>
          <select value={platform} onChange={e => setPlatform(e.target.value)}>
            <option value="">Every job site</option>
            {platforms.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="pipeline-field">
          <span>Experience level</span>
          <select value={seniority} onChange={e => setSeniority(e.target.value as SeniorityFilter)}>
            <option value="all">All levels</option>
            <option value="beginner">Entry level</option>
            <option value="fresher">Graduate / fresher</option>
            <option value="junior">Junior</option>
            <option value="mid">Mid-level</option>
            <option value="senior">Senior</option>
            <option value="unknown">Not specified</option>
          </select>
        </label>
        <LocationFilter
          value={location}
          onChange={setLocation}
          api={api}
          suggestions={locationSuggestions}
        />
        <label className="pipeline-field">
          <span>Sort</span>
          <select value={sort} onChange={e => setSort(e.target.value as LeadSort)}>
            <option value="recommended">Recommended</option>
            <option value="newest">Newest</option>
            <option value="signal">Most promising</option>
            <option value="match">Best match</option>
            <option value="company">Company</option>
          </select>
        </label>
      </div>

      <div className="pipeline-filter-actions">
        <span className="pipeline-count">{shown} of {total}</span>
        {hasFilters && <button className="pipeline-clear" onClick={resetFilters}>Clear filters</button>}
      </div>
      {actions && <div className="pipeline-actions">{actions}</div>}
    </div>
  );
}
