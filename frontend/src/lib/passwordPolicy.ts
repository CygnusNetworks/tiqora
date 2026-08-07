/**
 * Mirror of the backend's `domain/password_policy.py`.
 *
 * The backend is authoritative and rejects out-of-range passwords with a 422;
 * these constants exist so the forms can say the rule up front instead of
 * letting someone type a password and only then be told it is too short.
 */
export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 64;
