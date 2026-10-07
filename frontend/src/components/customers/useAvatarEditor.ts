import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "@/lib/api";
import { apiErrorMessage, type AvatarUploadOut } from "@/lib/avatarApi";
import { isAvatarImage } from "@/lib/useFileDrop";

const MAX_BYTES = 2 * 1024 * 1024;

export type AvatarEditor = {
  /** Cache buster for `<Avatar version>`; changes after every upload/removal. */
  version: string | null;
  busy: boolean;
  error: string | null;
  setError: (message: string | null) => void;
  uploadFile: (file: File) => Promise<void>;
  removeImage: () => Promise<void>;
  /** Show a new version without an upload of our own (vCard photo). */
  bump: (version?: string) => void;
};

/**
 * Upload / remove state of one avatar (customer photo or company logo).
 * The panels are keyed by customer, so the version starts fresh per subject.
 */
export function useAvatarEditor({
  upload,
  remove,
}: {
  upload: (file: File) => Promise<AvatarUploadOut>;
  remove: () => Promise<void>;
}): AvatarEditor {
  const { t } = useTranslation();
  const [version, setVersion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fail = (err: unknown) =>
    setError(
      err instanceof ApiError && err.status === 413
        ? t("avatars.tooLarge")
        : apiErrorMessage(err, t("avatars.uploadError")),
    );

  const uploadFile = async (file: File) => {
    // Checked here too so a wrong drop gets a clear message without a round trip.
    if (!isAvatarImage(file)) {
      setError(t("avatars.wrongType"));
      return;
    }
    if (file.size > MAX_BYTES) {
      setError(t("avatars.tooLarge"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const out = await upload(file);
      setVersion(out?.version || String(Date.now()));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const removeImage = async () => {
    setBusy(true);
    setError(null);
    try {
      await remove();
      // A new version so the browser does not keep showing the cached picture.
      setVersion(String(Date.now()));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  return {
    version,
    busy,
    error,
    setError,
    uploadFile,
    removeImage,
    bump: (next) => setVersion(next || String(Date.now())),
  };
}
