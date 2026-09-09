import { useEffect, useState } from "react";
import { version as packageVersion } from "../../../package.json";
import { platform } from "../lib/platform";

/**
 * The running app's version.
 *
 * Starts from package.json so there is always something to render, then asks the platform for the
 * authoritative value: the Tauri shell knows its own bundle version, and the web shell asks the API
 * (which is the thing that was actually deployed). A failure keeps the package.json fallback rather
 * than blanking the display.
 */
export function useAppVersion() {
  const [version, setVersion] = useState(packageVersion);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const value = await (await platform()).getVersion();
        if (alive && value) setVersion(value);
      } catch {
        /* keep the package.json fallback */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  return version;
}
