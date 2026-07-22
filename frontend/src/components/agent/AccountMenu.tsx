import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/auth/AuthContext";
import { useTheme } from "@/themes/theme";
import { Menu, MenuHeader, MenuItem, MenuLabel, MenuSeparator } from "@/components/ui/Menu";
import { SelectMenu } from "@/components/ui/SelectMenu";
import { Avatar } from "@/components/ui/Avatar";
import {
  ChevronDownIcon,
  GlobeIcon,
  LogOutIcon,
  MoonIcon,
  SettingsIcon,
  ShieldIcon,
  SunIcon,
} from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { userEmailForAvatar } from "@/lib/gravatar";

const LANGUAGES = [
  { code: "de", label: "Deutsch" },
  { code: "en", label: "English" },
] as const;

/**
 * Avatar dropdown for account actions, shared by the agent and admin shells.
 * Opens a Menu with the signed-in identity, a link to security / 2FA settings
 * (general preferences live in the sidebar), a language picker (Deutsch /
 * English, via the portal-based `SelectMenu`), a light/dark theme toggle,
 * and finally sign-out.
 * Admins additionally get a highlighted "Admin-Bereich" entry.
 *
 * `logoutTestId` keeps the existing `logout-btn` (mobile) / `logout-btn-desktop`
 * (desktop) hooks on the sign-out item so the shell tests keep passing.
 */
export function AccountMenu({ logoutTestId = "logout-btn" }: { logoutTestId?: string }) {
  const { t, i18n } = useTranslation();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();

  const currentLang = i18n.language?.startsWith("de") ? "de" : "en";
  const initials = (
    (user?.first_name?.[0] ?? user?.login?.[0] ?? "?") + (user?.last_name?.[0] ?? "")
  ).toUpperCase();
  const fullName = [user?.first_name || user?.login, user?.last_name].filter(Boolean).join(" ");
  const email = userEmailForAvatar(user);
  const avatarUrl = user?.avatar_url ?? null;
  const isAdmin = user?.is_admin === true;

  const changeLang = (code: string) => {
    void i18n.changeLanguage(code);
    localStorage.setItem("tiqora-lang", code);
  };

  return (
    <Menu
      panelTestId="account-menu"
      trigger={({ open, ref, toggleProps }) => (
        <button
          ref={ref}
          type="button"
          data-testid="account-menu-trigger"
          aria-label={t("account.menu")}
          className={cn(
            "flex h-8 items-center gap-1.5 rounded-lg pl-1 pr-1.5 transition-colors duration-100 hover:bg-surface-subtle focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent",
            open && "bg-surface-subtle",
          )}
          {...toggleProps}
        >
          {/* Always-present identity hook for e2e / assistive tech (was the sidebar user card). */}
          <span className="sr-only" data-testid="current-user">
            {fullName || user?.login}
          </span>
          <Avatar
            avatarUrl={avatarUrl}
            email={email}
            initials={initials}
            size={24}
            testId="account-menu-avatar"
          />
          <ChevronDownIcon
            className={cn(
              "text-[15px] text-muted transition-transform duration-150",
              open && "rotate-180",
            )}
          />
        </button>
      )}
    >
      <MenuHeader>
        <p className="truncate text-[13px] font-semibold text-ink" data-testid="account-menu-name">
          {fullName || user?.login}
        </p>
        <p className="truncate text-[11.5px] text-muted">{user?.login}</p>
      </MenuHeader>

      {isAdmin && (
        <div className="pt-1">
          <MenuItem
            highlight
            icon={<SettingsIcon />}
            testId="account-menu-admin"
            onSelect={() => void navigate({ to: "/admin" })}
          >
            {t("account.adminArea")}
          </MenuItem>
        </div>
      )}

      <div className={isAdmin ? "pt-0.5" : "pt-1"}>
        <MenuItem
          icon={<ShieldIcon />}
          testId="account-menu-security"
          onSelect={() => void navigate({ to: "/agent/security" })}
        >
          {t("account.security")}
        </MenuItem>
      </div>

      <MenuLabel>{t("account.language")}</MenuLabel>
      {/* Portal-based SelectMenu instead of a nested Menu — the account
          menu's own panel is overflow-hidden, which would clip a plain
          flyout; the portal panel renders above everything instead. The
          surrounding Menu stays open on selection (Menu.tsx's outside-click
          handler ignores `[data-portal-menu]`). */}
      <SelectMenu
        items={LANGUAGES.map((lang) => ({ value: lang.code, label: lang.label }))}
        value={currentLang}
        onSelect={changeLang}
        panelTestId="account-menu-lang-panel"
        trigger={({ open, ref, toggleProps }) => (
          <button
            ref={ref}
            type="button"
            data-testid="account-menu-lang-select"
            aria-label={t("account.language")}
            {...toggleProps}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-ink/90 transition-colors duration-100 hover:bg-surface-subtle focus:outline-none",
              open && "bg-surface-subtle",
            )}
          >
            <span className="flex w-4 shrink-0 justify-center text-[15px] text-muted" aria-hidden>
              <GlobeIcon />
            </span>
            <span className="min-w-0 flex-1 truncate">
              {LANGUAGES.find((lang) => lang.code === currentLang)?.label}
            </span>
            <span
              className={cn("text-muted transition-transform duration-150", open && "rotate-180")}
              aria-hidden
            >
              ⌄
            </span>
          </button>
        )}
      />

      <MenuLabel>{t("account.theme")}</MenuLabel>
      <MenuItem
        icon={<SunIcon />}
        keepOpen
        selected={theme === "light"}
        testId="account-menu-theme-light"
        onSelect={() => setTheme("light")}
      >
        {t("account.themeLight")}
      </MenuItem>
      <MenuItem
        icon={<MoonIcon />}
        keepOpen
        selected={theme === "dark"}
        testId="account-menu-theme-dark"
        onSelect={() => setTheme("dark")}
      >
        {t("account.themeDark")}
      </MenuItem>

      <MenuSeparator />
      <MenuItem
        danger
        icon={<LogOutIcon />}
        testId={logoutTestId}
        onSelect={() => {
          void logout().then(() => navigate({ to: "/login" }));
        }}
      >
        {t("auth.logout")}
      </MenuItem>
    </Menu>
  );
}
