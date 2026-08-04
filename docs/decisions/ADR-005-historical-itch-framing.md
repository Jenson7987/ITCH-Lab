# ADR-005 — Historical ITCH framing and termination

## Status

Accepted for MVP.

## Context

The Nasdaq BinaryFILE 1.00 specification describes records as a two-byte big-endian payload
length followed by the payload. It also describes a zero-length record as an end-of-session marker
and a file without that marker as incomplete. Before TASK-004, ITCH-Lab instead assumed that a
complete frame followed by physical end of file was clean and that zero length was invalid.

TASK-004 verified the public Nasdaq TotalView-ITCH 5.0 sample
`12302019.NASDAQ_ITCH50.gz`, downloaded from
`https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/12302019.NASDAQ_ITCH50.gz` on 2026-08-04.
The stored gzip file is 3,524,013,057 bytes and has SHA-256
`ef03df46a27e6bda4dead017f84c2e3979df7211f02c7868b51d53fceb99c689`.

A bounded probe confirmed a 12-byte `S` payload at uncompressed offset 0 followed by 39-byte `R`
payloads at offsets 14, 55, 96 and onward. A complete gzip drain validated the member CRC and
trailer. The uncompressed stream ends with the two-byte length `00 0c` and a complete 12-byte `S`
end-of-messages payload whose event code is `C`; it has no zero-length terminator. The raw sample
remains in the ignored local raw-data directory and is not committed.

## Decision

Define project framing `itch-length-v1` as follows:

- Every message is a positive two-byte big-endian payload length followed by exactly that many
  payload bytes.
- Physical end of the uncompressed stream immediately after a complete payload is clean end of
  input.
- Physical end after one prefix byte or within a declared payload is `ERR_TRUNCATED_MESSAGE`.
- A zero length is `ERR_FRAMING`; it is not exposed as a message or accepted as a terminator.
- Payload lengths above the project hard cap of 512 bytes are `ERR_FRAMING` before payload access
  or allocation. This is an ITCH-Lab safety limit, not a BinaryFILE protocol maximum.
- gzip input is complete only after zlib validates the gzip member trailer. gzip header, stream,
  checksum and trailer failures are `ERR_FRAMING`, even when earlier decompressed frames were
  readable.
- Source offsets are zero-based positions of frame-length prefixes in the uncompressed framed
  stream. Message indices start at zero and are assigned only to complete frames.

## Alternatives considered

- Require the BinaryFILE zero-length terminator: rejected because the authorised official ITCH
  5.0 sample would be classified as incomplete despite a valid gzip trailer and complete
  end-of-messages record.
- Accept both zero length and boundary EOF as clean: rejected because zero length is not observed
  in the verified sample and accepting two termination forms weakens corruption detection.
- Remove the 512-byte cap because BinaryFILE has no maximum: rejected because every supported
  ITCH 5.0 payload is substantially smaller and the cap is a deliberate untrusted-input control.

## Consequences

Positive:

- Production behaviour matches the verified official sample.
- Clean EOF, truncated frames and gzip corruption remain distinct typed outcomes.
- A fixed frame buffer bounds memory independently of declared source lengths.

Negative:

- ITCH-Lab deliberately differs from BinaryFILE 1.00 zero-terminator wording.
- A future Nasdaq historical format that emits a zero terminator will be rejected until its format
  is verified and this ADR is revisited.

## Conditions that justify revisiting

- An authorised Nasdaq TotalView-ITCH 5.0 source uses a zero-length terminator.
- Nasdaq publishes a revised historical-file contract that matches delivered files differently.
- A supported future ITCH message requires more than 512 payload bytes.
