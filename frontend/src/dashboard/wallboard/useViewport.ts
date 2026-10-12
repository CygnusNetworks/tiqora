import { useEffect, useState } from "react";

function read(): { width: number; height: number } {
  return { width: window.innerWidth || 1920, height: window.innerHeight || 1080 };
}

/** The window size, updated on resize (a TV switching resolution refits the board). */
export function useViewport(): { width: number; height: number } {
  const [size, setSize] = useState(read);
  useEffect(() => {
    const onResize = () => {
      const next = read();
      setSize((prev) => (prev.width === next.width && prev.height === next.height ? prev : next));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return size;
}
