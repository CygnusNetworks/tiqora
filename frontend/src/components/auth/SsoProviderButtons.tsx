import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import type { LoginProvider } from "@/lib/identityApi";
import { ProviderIcon } from "./ProviderIcon";

/** One sign-in button per identity provider; a click leaves the SPA for `hrefFor(slug)`. */
export function SsoProviderButtons({
  providers,
  hrefFor,
  onBeforeNavigate,
  testIdPrefix = "sso-provider",
}: {
  providers: LoginProvider[];
  hrefFor: (slug: string) => string;
  onBeforeNavigate?: (provider: LoginProvider) => void;
  testIdPrefix?: string;
}) {
  const { t } = useTranslation();
  if (providers.length === 0) return null;
  return (
    <>
      {providers.map((p) => (
        <Button
          key={p.slug}
          type="button"
          variant="secondary"
          className="w-full"
          data-testid={`${testIdPrefix}-${p.slug}`}
          onClick={() => {
            onBeforeNavigate?.(p);
            window.location.assign(hrefFor(p.slug));
          }}
        >
          <ProviderIcon icon={p.icon} />
          {p.label || t("auth.providers.defaultLabel")}
        </Button>
      ))}
    </>
  );
}
