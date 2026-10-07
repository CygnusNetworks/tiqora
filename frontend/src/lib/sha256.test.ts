import { describe, expect, it } from "vitest";
import { sha256 } from "@/lib/sha256";

describe("sha256", () => {
  it("matches the FIPS 180-4 / NIST vectors", () => {
    expect(sha256("")).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(sha256("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    // Two-block message.
    expect(sha256("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")).toBe(
      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    );
  });

  it("hashes the UTF-8 bytes of non-ASCII input", () => {
    expect(sha256("müller@bäcker.de")).toBe(
      "5d582447018c546b502f0d104cf7d16d0cf176acdcaed27b1429d7ef39a62c36",
    );
  });

  it("handles long multi-block input", () => {
    expect(sha256("a".repeat(1000))).toBe(
      "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3",
    );
    expect(sha256("jane@example.com")).toBe(
      "8c87b489ce35cf2e2f39f80e282cb2e804932a56a213983eeeb428407d43b52d",
    );
  });
});
