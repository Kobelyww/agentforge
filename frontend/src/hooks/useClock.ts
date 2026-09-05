import { useEffect, useState } from "react";

/** HUD 实时时钟（UTC），独立于 3D 场景模块以便代码分割。 */
export function useElapsed(refreshMs = 1000): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), refreshMs);
    return () => window.clearInterval(timer);
  }, [refreshMs]);
  return now.toUTCString().slice(17, 25) + " UTC";
}
