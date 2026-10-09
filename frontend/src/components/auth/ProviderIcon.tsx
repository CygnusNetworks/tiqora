import type { SVGProps } from "react";
import {
  BuildingIcon,
  FolderIcon,
  KeyIcon,
  ShieldIcon,
} from "@/components/ui/icons";

/** Four-square Microsoft mark in the brand colours. */
function MicrosoftMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" {...props}>
      <rect x="2" y="2" width="9.5" height="9.5" fill="#f25022" />
      <rect x="12.5" y="2" width="9.5" height="9.5" fill="#7fba00" />
      <rect x="2" y="12.5" width="9.5" height="9.5" fill="#00a4ef" />
      <rect x="12.5" y="12.5" width="9.5" height="9.5" fill="#ffb900" />
    </svg>
  );
}

/** Google "G" mark in the brand colours. */
function GoogleMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" {...props}>
      <path
        fill="#4285f4"
        d="M22.5 12.27c0-.79-.07-1.54-.2-2.27H12v4.3h5.9a5.05 5.05 0 0 1-2.19 3.31v2.75h3.54c2.07-1.91 3.25-4.72 3.25-8.09z"
      />
      <path
        fill="#34a853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.54-2.75c-.98.66-2.24 1.05-3.74 1.05-2.87 0-5.3-1.94-6.17-4.55H2.17v2.84A11 11 0 0 0 12 23z"
      />
      <path
        fill="#fbbc05"
        d="M5.83 14.09a6.6 6.6 0 0 1 0-4.18V7.07H2.17a11 11 0 0 0 0 9.86z"
      />
      <path
        fill="#ea4335"
        d="M12 5.36c1.62 0 3.07.56 4.21 1.65l3.15-3.15C17.45 2.09 14.97 1 12 1A11 11 0 0 0 2.17 7.07l3.66 2.84C6.7 7.3 9.13 5.36 12 5.36z"
      />
    </svg>
  );
}

/** Icon of an SSO sign-in button, chosen by the provider's `icon` name. */
export function ProviderIcon({
  icon,
  className = "h-4 w-4 shrink-0",
}: {
  icon: string | null | undefined;
  className?: string;
}) {
  switch (icon) {
    case "microsoft":
      return <MicrosoftMark className={className} />;
    case "google":
      return <GoogleMark className={className} />;
    case "shield":
      return <ShieldIcon className={className} aria-hidden="true" />;
    case "building":
      return <BuildingIcon className={className} aria-hidden="true" />;
    case "directory":
      return <FolderIcon className={className} aria-hidden="true" />;
    default:
      return <KeyIcon className={className} aria-hidden="true" />;
  }
}
