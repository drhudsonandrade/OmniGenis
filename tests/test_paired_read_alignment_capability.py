"""Synthetic contract cases for paired-read alignment.

Constructed SAM/BAM/BAI vectors exercise validation and publication boundaries.
They are not evidence that a native aligner ran or that human mapping is accurate.
The physical encoders below follow the pinned SAM/BAM specification independently
of the capability implementation.
"""
from collections import Counter
from contextlib import ExitStack
import copy
from dataclasses import fields, FrozenInstanceError
import gzip
import hashlib
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "paired_read_alignment"
CONTIGS = (("unit_ref", 50000),)
RG = {"ID": "synthetic:alignment-rg", "SM": "synthetic:alignment-sample", "PL": "ILLUMINA"}
CIGAR_CODES = "MIDNSHP=X"
BAM_CODES = "=ACMGRSVTWYHKDBN"
BGZF_EOF = bytes.fromhex("1f8b08040000000000ff0600424302001b0003000000000000000000")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def reverse_complement(value):
    return value.translate(bytes.maketrans(b"ACGTN", b"TGCAN"))[::-1]


def module():
    return importlib.import_module("omnigenis.capabilities.paired_read_alignment")


def fixture_pair():
    return ((FIXTURES / "valid-r1.fastq").read_bytes(), (FIXTURES / "valid-r2.fastq").read_bytes())


def bound_profile(r1, r2):
    value = json.loads((FIXTURES / "input-profile.json").read_text())
    for name, data in (("r1", r1), ("r2", r2)):
        value[name]["content_sha256"] = digest(data)
        value[name]["size_bytes"] = len(data)
    return value


def execution_profile():
    return json.loads((ROOT / "resources/execution-profiles/paired-fastq-alignment-v1.json").read_text())


def synthetic_reference():
    seed = b"OmniGenis synthetic alignment unit reference v1"
    result = bytearray()
    counter = 0
    while len(result) < 50000:
        result.extend(b"ACGT"[byte & 3] for byte in hashlib.sha256(seed + b":" + str(counter).encode()).digest())
        counter += 1
    return bytes(result[:50000])


def fastq_records(data, mate):
    lines = data.splitlines()
    assert len(lines) % 4 == 0
    return [(lines[i].split(b" ", 1)[0][1:], mate, lines[i + 1], lines[i + 3])
            for i in range(0, len(lines), 4)]


def make_pair_at(starts, *, read_length=150, eol=b"\n", include_n=False):
    reference = synthetic_reference()
    streams = [bytearray(), bytearray()]
    for ordinal, start in enumerate(starts):
        for mate in (1, 2):
            left = start if mate == 1 else start + 250
            sequence = reference[left:left + read_length]
            if mate == 2:
                sequence = reverse_complement(sequence)
            if include_n:
                sequence = sequence[:2] + b"N" + sequence[3:]
            quality = bytes(33 + (7 * j + ordinal + mate) % 41 for j in range(len(sequence)))
            header = f"@SYNTHALIGN:1:UNITONLY:1:1101:{ordinal + 1}:{ordinal + 1} {mate}:N:0:ACGT".encode()
            streams[mate - 1].extend(eol.join((header, sequence, b"+", quality)) + eol)
    return bytes(streams[0]), bytes(streams[1])


def record_vectors(r1, r2, starts=None):
    left, right = fastq_records(r1, 1), fastq_records(r2, 2)
    starts = starts if starts is not None else [600 + i * 311 for i in range(len(left))]
    records = []
    for ordinal, (one, two) in enumerate(zip(left, right)):
        start = starts[ordinal]
        for qname, mate, sequence, quality in (one, two):
            reverse = mate == 2
            records.append({
                "qname": qname, "flag": 147 if reverse else 99, "ref": 0,
                "pos": start + (250 if reverse else 0), "mapq": 60,
                "cigar": [(len(sequence), "M")], "next_ref": 0,
                "next_pos": start + (0 if reverse else 250),
                "tlen": -(250 + len(sequence)) if reverse else 250 + len(sequence),
                "seq": reverse_complement(sequence) if reverse else sequence,
                "qual": quality[::-1] if reverse else quality,
                "tags": [("NM", "i", sequence.count(b"N")), ("AS", "i", len(sequence) - sequence.count(b"N")),
                         ("RG", "Z", RG["ID"])],
            })
    return records


def header(contigs=CONTIGS, read_group=RG, *, coordinate=True, command=None):
    lines = ["@HD\tVN:1.6\tSO:" + ("coordinate" if coordinate else "unsorted")]
    lines += [f"@SQ\tSN:{name}\tLN:{length}" for name, length in contigs]
    lines.append("@RG\tID:" + read_group["ID"] + "\tSM:" + read_group["SM"] + "\tPL:" + read_group["PL"])
    pg = "@PG\tID:bwa-mem2\tPN:bwa-mem2\tVN:2.2.1"
    if command is not None:
        pg += "\tCL:" + command
    lines.append(pg)
    return ("\n".join(lines) + "\n").encode("ascii")


def sam_record(row, contigs=CONTIGS):
    reference = "*" if row["ref"] < 0 else contigs[row["ref"]][0]
    mate_reference = ("*" if row["next_ref"] < 0 else "=" if row["next_ref"] == row["ref"]
                      else contigs[row["next_ref"]][0])
    cigar = "".join(str(size) + op for size, op in row["cigar"]) or "*"
    seq = row["seq"].decode() if row["seq"] else "*"
    qual = row["qual"].decode() if row["qual"] is not None else "*"
    values = [row["qname"].decode(), str(row["flag"]), reference, str(row["pos"] + 1),
              str(row["mapq"]), cigar, mate_reference, str(row["next_pos"] + 1),
              str(row["tlen"]), seq, qual]
    for name, kind, value in row["tags"]:
        if kind == "B":
            subtype, items = value
            text = subtype + "," + ",".join(map(str, items))
        else:
            text = str(value)
        values.append(name + ":" + kind + ":" + text)
    return ("\t".join(values) + "\n").encode("ascii")


def reg2bin(start, end):
    end = max(start + 1, end) - 1
    for shift, base in ((14, 4681), (17, 585), (20, 73), (23, 9), (26, 1)):
        if start >> shift == end >> shift:
            return base + (start >> shift)
    return 0


def reference_end(row):
    return row["pos"] + sum(size for size, op in row["cigar"] if op in "MDN=X")


def auxiliary_bytes(tags):
    result = bytearray()
    formats = {"c": "b", "C": "B", "s": "h", "S": "H", "i": "i", "I": "I", "f": "f"}
    for name, kind, value in tags:
        result.extend(name.encode() + kind.encode())
        if kind in formats:
            result.extend(struct.pack("<" + formats[kind], value))
        elif kind == "A":
            result.extend(value.encode("ascii"))
        elif kind in ("Z", "H"):
            result.extend(value.encode("ascii") + b"\0")
        elif kind == "B":
            subtype, items = value
            result.extend(subtype.encode() + struct.pack("<i", len(items)))
            result.extend(struct.pack("<" + formats[subtype] * len(items), *items))
        else:
            raise AssertionError("Unsupported test-vector type")
    return bytes(result)


def bam_record(row):
    qname = row["qname"] + b"\0"
    sequence = row["seq"]
    base_codes = [BAM_CODES.index(chr(base)) for base in sequence]
    packed = bytes((base_codes[i] << 4) | (base_codes[i + 1] if i + 1 < len(base_codes) else 0)
                   for i in range(0, len(base_codes), 2))
    qualities = bytes([255]) * len(sequence) if row["qual"] is None else bytes(x - 33 for x in row["qual"])
    bin_id = reg2bin(max(0, row["pos"]), max(1, reference_end(row))) if row["ref"] >= 0 else 4680
    core = struct.pack("<iiIIiiii", row["ref"], row["pos"],
                       (bin_id << 16) | (row["mapq"] << 8) | len(qname),
                       (row["flag"] << 16) | len(row["cigar"]), len(sequence),
                       row["next_ref"], row["next_pos"], row["tlen"])
    cigar = b"".join(struct.pack("<I", (size << 4) | CIGAR_CODES.index(op)) for size, op in row["cigar"])
    body = core + qname + cigar + packed + qualities + auxiliary_bytes(row["tags"])
    return struct.pack("<i", len(body)) + body


def bgzf_block(data):
    compressor = zlib.compressobj(level=6, wbits=-15)
    payload = compressor.compress(data) + compressor.flush()
    total = 18 + len(payload) + 8
    assert total <= 65536
    prefix = b"\x1f\x8b\x08\x04" + b"\0" * 4 + b"\0\xff" + struct.pack("<H", 6)
    prefix += b"BC" + struct.pack("<HH", 2, total - 1)
    return prefix + payload + struct.pack("<II", zlib.crc32(data) & 0xFFFFFFFF, len(data))


def encode_bam_and_bai(records, contigs=CONTIGS, read_group=RG):
    text = header(contigs, read_group)
    raw = bytearray(b"BAM\1" + struct.pack("<i", len(text)) + text + struct.pack("<i", len(contigs)))
    for name, length in contigs:
        encoded = name.encode() + b"\0"
        raw.extend(struct.pack("<i", len(encoded)) + encoded + struct.pack("<i", length))
    positions = []
    for row in records:
        start = len(raw)
        raw.extend(bam_record(row))
        positions.append((start, len(raw)))
    compressed, blocks = bytearray(), []
    for offset in range(0, len(raw), 16000):
        data = bytes(raw[offset:offset + 16000])
        block = bgzf_block(data)
        blocks.append((offset, len(data), len(compressed), len(block)))
        compressed.extend(block)
    eof_offset = len(compressed)
    compressed.extend(BGZF_EOF)

    def virtual(offset):
        if offset == len(raw):
            return eof_offset << 16
        for start, length, packed_start, _ in blocks:
            if start <= offset < start + length:
                return (packed_start << 16) | (offset - start)
        raise AssertionError("Invalid independent virtual offset")

    indexed_positions = [(virtual(start), virtual(end)) for start, end in positions]
    bai = bytearray(b"BAI\1" + struct.pack("<i", len(contigs)))
    for ref in range(len(contigs)):
        bins = {}
        linear = []
        mapped = placed_unmapped = 0
        ref_offsets = []
        for row, offsets in zip(records, indexed_positions):
            if row["ref"] != ref:
                continue
            ref_offsets.append(offsets)
            if row["flag"] & 4:
                placed_unmapped += 1
                end = row["pos"] + 1
            else:
                mapped += 1
                end = reference_end(row)
                for window in range(row["pos"] >> 14, ((max(row["pos"] + 1, end) - 1) >> 14) + 1):
                    while len(linear) <= window:
                        linear.append(0)
                    if not linear[window] or offsets[0] < linear[window]:
                        linear[window] = offsets[0]
            bins.setdefault(reg2bin(row["pos"], end), []).append(offsets)
        if ref_offsets:
            bins[37450] = [(ref_offsets[0][0], ref_offsets[-1][1]), (mapped, placed_unmapped)]
        bai.extend(struct.pack("<i", len(bins)))
        for bin_id, chunks in sorted(bins.items()):
            bai.extend(struct.pack("<Ii", bin_id, len(chunks)))
            for begin, end in chunks:
                bai.extend(struct.pack("<QQ", begin, end))
        bai.extend(struct.pack("<i", len(linear)))
        for offset in linear:
            bai.extend(struct.pack("<Q", offset))
    bai.extend(struct.pack("<Q", sum(row["ref"] < 0 for row in records)))
    return bytes(compressed), bytes(bai), blocks


def write_vectors(directory, records, *, contigs=CONTIGS, read_group=RG):
    directory.mkdir()
    ordered = sorted(records, key=lambda row: (
        row["ref"] if row["ref"] >= 0 else 2**31,
        row["pos"] if row["pos"] >= 0 else 2**31, row["qname"], row["flag"]))
    bam, bai, blocks = encode_bam_and_bai(ordered, contigs, read_group)
    original = header(contigs, read_group, coordinate=False) + b"".join(sam_record(row, contigs) for row in records)
    decoded = header(contigs, read_group) + b"".join(sam_record(row, contigs) for row in ordered)
    values = {"sam_path": original, "bam_path": bam, "bai_path": bai,
              "decoded_path": decoded, "indexed_path": decoded}
    names = {"sam_path": "original.sam", "bam_path": "result.bam", "bai_path": "result.bam.bai",
             "decoded_path": "decoded.sam", "indexed_path": "indexed.sam"}
    paths = {}
    for key, data in values.items():
        p = directory / names[key]
        p.write_bytes(data)
        paths[key] = p
    return paths, blocks


def constructed_accepted(snapshot, r1, r2, paths, accounting):
    """Build a schema vector; published identities here do not qualify runtime."""
    alignment = json.loads((ROOT / "resources/alignment-profiles/paired-fastq-grch38-p14-bwa-mem2-v1.json").read_text())
    reference = json.loads((ROOT / "resources/reference-profiles/grch38-p14-ncbi-refseq-full-alignment-v1.json").read_text())
    executor = json.loads((ROOT / "resources/executor-profiles/bwa-mem2-2.3-sse41-samtools-1.25-v1.json").read_text())
    reference_binding = {
        "profile_id": reference["profile_id"],
        "profile_sha256": digest((ROOT / "resources/reference-profiles/grch38-p14-ncbi-refseq-full-alignment-v1.json").read_bytes()),
        "fasta_sha256": reference["fasta"]["sha256"], "fai_sha256": reference["fai"]["sha256"],
        "dictionary_sha256": reference["dictionary"]["sha256"],
        "index_files": {key: value["sha256"] for key, value in reference["index_files"].items()},
        "contig_dictionary_sha256": reference["contig_dictionary_sha256"],
    }
    executor_binding = {
        "profile_id": executor["profile_id"],
        "profile_sha256": digest((ROOT / "resources/executor-profiles/bwa-mem2-2.3-sse41-samtools-1.25-v1.json").read_bytes()),
        "bwa_sha256": executor["executors"]["bwa"]["sha256"],
        "samtools_sha256": executor["executors"]["samtools"]["sha256"],
        "runtime_libraries_sha256": executor["runtime_libraries_sha256"],
    }
    parameter_digest = digest(canonical(alignment["parameters"]))
    profile = bound_profile(r1, r2)
    declaration = {key: value for key, value in profile.items() if key.startswith("declared_")}
    parents = [
        "fastq-r1:sha256:" + digest(r1), "fastq-r2:sha256:" + digest(r2),
        "input-profile:sha256:" + snapshot.input_profile_sha256,
        "parameters:sha256:" + parameter_digest,
        "reference-binding:sha256:" + digest(canonical(reference_binding)),
        "executor-binding:sha256:" + digest(canonical(executor_binding)),
    ]

    def artifact(kind, value, parent_ids):
        return {"schema_version": "1.0.0", "artifact_id": kind.lower() + ":sha256:" + digest(value),
                "artifact_kind": kind, "media_type": "application/octet-stream", "sha256": digest(value),
                "size_bytes": len(value), "producer_execution_profile_id": "paired-fastq-alignment-v1",
                "parent_artifact_ids": parent_ids}
    bam, bai = paths["bam_path"].read_bytes(), paths["bai_path"].read_bytes()
    bam_artifact = artifact("BAM", bam, parents)
    bai_artifact = artifact("BAI", bai, [bam_artifact["artifact_id"]])
    return {
        "input_profile_sha256": snapshot.input_profile_sha256,
        "input_mates": {"r1": {"content_sha256": digest(r1), "size_bytes": len(r1)},
                        "r2": {"content_sha256": digest(r2), "size_bytes": len(r2)}},
        "pair_count": snapshot.pair_count, "reference_binding": reference_binding,
        "executor_binding": executor_binding, "parameter_profile_sha256": parameter_digest,
        "declared_identity_binding_sha256": digest(canonical(declaration)),
        "bam_artifact": bam_artifact, "bai_artifact": bai_artifact,
        "accounting": accounting, "assessments": alignment["assessments"],
    }


class PairedReadAlignmentContractTests(unittest.TestCase):
    def setUp(self):
        self.api = module()
        self.temporary = tempfile.TemporaryDirectory(prefix="alignment-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.counter = 0

    def snapshot(self, r1=None, r2=None, *, profile=None):
        if r1 is None:
            r1, r2 = fixture_pair()
        self.counter += 1
        workspace = self.work / ("snapshot-" + str(self.counter))
        workspace.mkdir()
        selected_profile = bound_profile(r1, r2) if profile is None else profile
        value = self.api._snapshot_inputs(r1, r2, selected_profile, workspace, time.monotonic() + 30)
        self.addCleanup(value.close)
        return value

    def vectors(self, records, **kwargs):
        self.counter += 1
        return write_vectors(self.work / ("vectors-" + str(self.counter)), records, **kwargs)

    def validate(self, snapshot, paths):
        return self.api._validate_alignment_outputs(
            snapshot, contigs=CONTIGS, read_group=RG, **paths, deadline=time.monotonic() + 30,
        )

    def failure(self, code, callback):
        with self.assertRaises(self.api._Failure) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def unused_resources(self):
        return self.api.AlignmentResources(
            self.work / "reference.fna", self.work / "reference.fna.fai",
            self.work / "reference.dict", self.work / "reference-index",
            self.work / "bwa-mem2.sse41", self.work / "samtools", self.work,
        )

    def public_refusal(self, r1, r2, profile, code):
        with patch.object(self.api, "_run_native", side_effect=AssertionError("Native execution must not occur")):
            result = self.api.align_paired_fastq(
                r1, r2, profile, execution_profile=execution_profile(), resources=self.unused_resources(),
            )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.artifacts, ())
        payload = result.to_dict()
        self.assertEqual(payload["canonical_status"], "INVALID")
        self.assertEqual(payload["availability"], "AVAILABLE")
        self.assertIsNone(payload["canonical_payload"]["accepted_alignment"])
        self.assertEqual(payload["canonical_payload"]["diagnostics"], [{"code": code}])
        self.assertEqual(payload["raw_artifact_refs"], [])
        serialized = canonical(payload)
        self.assertNotIn(str(self.work).encode(), serialized)
        self.assertNotIn(b"SYNTHALIGN:", serialized)
        return result

    def small_limits(self, *, rss=256 * 1024**2, wall=5):
        return self.api._CommandLimits(
            address_space_bytes=512 * 1024**2, rss_bytes=rss, wall_seconds=wall,
            stdout_bytes=1024**2, stderr_bytes=1024**2, file_bytes=1024**2,
            disk_bytes=8 * 1024**2, free_disk_bytes=1024**2,
        )

    def run_small(self, code, limits):
        return self.api._run_native(
            [sys.executable, "-I", "-B", "-c", code], cwd=self.work,
            deadline=time.monotonic() + 10, limits=limits,
            stdout_path=self.work / "child.stdout", stderr_path=self.work / "child.stderr",
        )

    def test_t01_intake_positive_and_opt_in_entry(self):
        self.assertTrue(callable(self.api.align_paired_fastq))
        self.assertEqual(tuple(inspect.signature(self.api.align_paired_fastq).parameters),
                         ("r1", "r2", "input_profile", "execution_profile", "resources", "timeout_seconds"))
        self.assertEqual(tuple(field.name for field in fields(self.api.AlignmentResources)),
                         ("reference_fasta", "reference_fai", "reference_dictionary", "bwa_index_prefix",
                          "bwa_executable", "samtools_executable", "private_work_root"))
        self.assertTrue(self.api.AlignmentResources.__dataclass_params__.frozen)
        r1, r2 = fixture_pair()
        manifest = json.loads((FIXTURES / "fixture-manifest.json").read_text())
        for expected in manifest["files"]:
            value = (FIXTURES / expected["path"]).read_bytes()
            self.assertEqual((len(value), digest(value)), (expected["bytes"], expected["sha256"]))
        self.assertEqual(digest(synthetic_reference()), manifest["reference"]["sha256"])
        input_profile = json.loads((FIXTURES / "input-profile.json").read_text())
        snapshot = self.snapshot(r1, r2, profile=input_profile)
        self.assertEqual(snapshot.pair_count, 128)
        self.assertEqual(snapshot.expected_reads, Counter(fastq_records(r1, 1) + fastq_records(r2, 2)))
        self.assertEqual(snapshot.input_profile_sha256, digest(canonical(input_profile)))
        self.api._verify_snapshot(snapshot, time.monotonic() + 10)
        profile = json.loads((ROOT / "resources/alignment-profiles/paired-fastq-grch38-p14-bwa-mem2-v1.json").read_text())
        self.assertIs(profile["default_enabled"], False)
        selected_execution = execution_profile()
        integer_execution = dict(selected_execution, deterministic=1)
        with patch.object(self.api, "_workspace",
                          side_effect=AssertionError("Rejected profile must not create a workspace")) as workspace:
            refused = self.api.align_paired_fastq(
                r1, r2, input_profile, execution_profile=integer_execution,
                resources=self.unused_resources())
        workspace.assert_not_called()
        self.assertFalse(refused.is_valid)
        self.assertEqual(refused.artifacts, ())
        self.assertEqual(refused.to_dict()["canonical_payload"]["diagnostics"],
                         [{"code": "unsupported_execution_profile"}])
        reordered_execution = dict(reversed(tuple(selected_execution.items())))
        with patch.object(self.api, "_snapshot_inputs",
                          side_effect=self.api._Failure("input_intake_refused")) as intake:
            reached_intake = self.api.align_paired_fastq(
                r1, r2, input_profile, execution_profile=reordered_execution,
                resources=self.unused_resources())
        intake.assert_called_once()
        self.assertFalse(reached_intake.is_valid)
        self.assertEqual(reached_intake.to_dict()["canonical_payload"]["diagnostics"],
                         [{"code": "input_intake_refused"}])

    def test_t02_legacy_headers_refuse_before_native_work(self):
        r1, r2 = fixture_pair()
        r1 = b"@legacy/1\n" + r1.split(b"\n", 1)[1]
        self.public_refusal(r1, r2, bound_profile(r1, r2), "input_intake_refused")

    def test_t03_declared_digest_mismatch_refuses(self):
        r1, r2 = fixture_pair()
        profile = bound_profile(r1, r2)
        profile["r1"]["content_sha256"] = "0" * 64
        self.public_refusal(r1, r2, profile, "input_intake_refused")

    def test_t04_correctly_hashed_mate_mismatch_refuses(self):
        r1, r2 = fixture_pair()
        r2 = r2.replace(b":1101:1:1 2:", b":1101:999:1 2:", 1)
        self.public_refusal(r1, r2, bound_profile(r1, r2), "input_intake_refused")

        # A small owned child reads the already verified objects while the
        # operator-visible index directory contains different bytes.
        original, replacement, saved = (self.work / name for name in ("index-original", "index-other", "index-held"))
        original.mkdir()
        replacement.mkdir()
        expected, contents = {}, {}
        for suffix in self.api.INDEX_SUFFIXES:
            raw = ("verified-member:" + suffix).encode()
            contents[suffix] = raw
            (original / ("ref" + suffix)).write_bytes(raw)
            (replacement / ("ref" + suffix)).write_bytes(b"unverified-replacement")
            expected[suffix] = {"sha256": digest(raw), "size_bytes": len(raw)}
        child_work = self.work / "bound-index-child"
        child_work.mkdir()
        view = self.api._bind_index_view(original / "ref", child_work, expected, time.monotonic() + 10)
        self.addCleanup(view.close)
        self.failure("resource_limit_exceeded", lambda: self.api._owned_size(child_work))
        self.assertLess(self.api._owned_size(child_work, index_view=view), self.small_limits().disk_bytes)
        unowned = child_work / "unowned-link"
        unowned.symlink_to(original / ("ref" + self.api.INDEX_SUFFIXES[0]))
        self.failure("resource_limit_exceeded", lambda: self.api._owned_size(child_work, index_view=view))
        unowned.unlink()
        ready, release = child_work / "ready", child_work / "release"
        errors = []

        def replace_index_directory():
            try:
                until = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < until:
                    time.sleep(0.01)
                if not ready.exists():
                    raise AssertionError("Small child did not reach the controlled read boundary")
                original.rename(saved)
                replacement.rename(original)
            except BaseException as error:
                errors.append(error)
            finally:
                release.write_bytes(b"go")

        code = ("from pathlib import Path\nimport sys,time\n"
                "Path('ready').write_bytes(b'ready')\n"
                "while not Path('release').exists():time.sleep(0.01)\n"
                "prefix=sys.argv[1]\n"
                + "suffixes=" + repr(self.api.INDEX_SUFFIXES) + "\n"
                + "print(b''.join(Path(prefix+s).read_bytes()for s in suffixes).hex(),flush=True)\n")
        updater = threading.Thread(target=replace_index_directory, daemon=True)
        updater.start()
        try:
            receipt = self.api._run_native(
                [sys.executable, "-I", "-S", "-B", "-c", code, str(view.prefix)],
                cwd=child_work, deadline=time.monotonic() + 10, limits=self.small_limits(),
                stdout_path=child_work / "index.stdout", stderr_path=child_work / "index.stderr",
                pass_fds=view.fds, index_view=view)
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual((child_work / "index.stdout").read_text().strip(),
                             b"".join(contents[s] for s in self.api.INDEX_SUFFIXES).hex())
            self.assertEqual((original / "ref.0123").read_bytes(), b"unverified-replacement")
        finally:
            updater.join(4)
            self.assertFalse(updater.is_alive())
            if saved.exists():
                if original.exists():
                    original.rename(replacement)
                saved.rename(original)
            view.retire()
        self.assertFalse(errors)
        self.assertFalse(view.directory.exists())
        view.verify(time.monotonic() + 10)
        for suffix in self.api.INDEX_SUFFIXES:
            self.assertEqual((original / ("ref" + suffix)).read_bytes(), contents[suffix])
        changed = original / "ref.0123"
        changed.write_bytes(b"changed in place")
        changed.write_bytes(contents[".0123"])
        self.failure("index_identity_mismatch", lambda: view.verify(time.monotonic() + 10))

    def test_t05a_constructed_complete_stream_and_lineage(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        records = record_vectors(r1, r2)
        for row, original in zip(records[:2], (fastq_records(r1, 1)[0], fastq_records(r2, 2)[0])):
            row.update(flag=77 if original[1] == 1 else 141, ref=-1, pos=-1, mapq=0, cigar=[],
                       next_ref=-1, next_pos=-1, tlen=0, seq=original[2], qual=original[3],
                       tags=[("RG", "Z", RG["ID"])])
        paths, _ = self.vectors(records)
        with patch.object(self.api, "_parse_sam_record", wraps=self.api._parse_sam_record) as parse_record:
            accounting = self.validate(snapshot, paths)
        self.assertEqual(parse_record.call_count, 3 * len(records))
        shared_names = parse_record.call_args_list[0].args[2]
        self.assertEqual(shared_names, {b"unit_ref": 0})
        self.assertTrue(all(call.args[2] is shared_names for call in parse_record.call_args_list))
        shifted_contigs = (("unused_ref", 50000),) + CONTIGS
        mapped_record = next(row for row in records if row["ref"] >= 0)
        shifted = self.api._parse_sam_record(sam_record(mapped_record).rstrip(b"\n"), shifted_contigs,
                                             {b"unused_ref": 0, b"unit_ref": 1})
        self.assertEqual((shifted.rid, shifted.next_rid), (1, 1))
        self.assertEqual(accounting["pairs"], 128)
        self.assertEqual(accounting["primary"], 256)
        self.assertEqual(accounting["primary_mapped"], 254)
        self.assertEqual(accounting["primary_unmapped"], 2)
        self.assertEqual(accounting["no_coordinate"], 2)
        self.assertTrue(accounting["orientation_restored_multiplicity_equal_FASTQ"])
        accepted = constructed_accepted(snapshot, r1, r2, paths, accounting)
        result = self.api._promote(paths["bam_path"].read_bytes(), paths["bai_path"].read_bytes(), accepted)
        self.assertTrue(result.is_valid)
        self.assertEqual(tuple(x.artifact_kind for x in result.artifacts), ("BAM", "BAI"))
        self.assertEqual(result.artifacts[1].parent_artifact_ids, (result.artifacts[0].artifact_id,))
        self.assertEqual(len(result.artifacts[0].parent_artifact_ids), 6)
        for artifact in result.artifacts:
            self.assertIs(type(artifact.content), bytes)
            self.assertEqual(artifact.sha256, digest(artifact.content))
            self.assertEqual(artifact.size_bytes, len(artifact.content))
            self.assertEqual(artifact.to_manifest()["sha256"], artifact.sha256)
        exported = result.to_dict()
        exported["canonical_payload"]["diagnostics"].append({"code": "malformed_output"})
        self.assertEqual(result.to_dict()["canonical_payload"]["diagnostics"], [])
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            result.artifacts[0].content = b"changed"

    def test_t05b_constructed_positions_two_seven_and_contig_end(self):
        # Physical-coordinate vectors, not a substitute for a native human oracle.
        starts = [1, 6, CONTIGS[0][1] - 400]
        r1, r2 = make_pair_at(starts)
        snapshot = self.snapshot(r1, r2)
        records = record_vectors(r1, r2, starts)
        self.assertEqual([records[0]["pos"] + 1, records[2]["pos"] + 1], [2, 7])
        self.assertEqual(reference_end(records[-1]), CONTIGS[0][1])
        paths, _ = self.vectors(records)
        self.assertEqual(self.validate(snapshot, paths)["primary"], 6)

    def test_t06_changed_owned_snapshot_refuses(self):
        snapshot = self.snapshot()
        before = snapshot.r1_path.read_bytes()
        os.chmod(snapshot.r1_path, 0o600)
        snapshot.r1_path.write_bytes(before[:-1] + b"!")
        self.failure("input_identity_changed", lambda: self.api._verify_snapshot(snapshot, time.monotonic() + 10))

    def resource_gate_refusal(self, target_class):
        """Exercise the real resource gate; only unrelated observations are stubbed."""
        profiles = self.api._load_profiles()
        reference, executor = profiles["reference"], profiles["executor"]
        directory = self.work / "resource-gate"
        directory.mkdir()
        paths = {"fasta": directory / "reference.fna", "fai": directory / "reference.fna.fai",
                 "dictionary": directory / "reference.dict", "bwa": directory / "bwa-mem2.sse41",
                 "samtools": directory / "samtools"}
        paths["fasta"].write_bytes(b">independent-unit\nACGT\n")
        paths["fai"].write_text("".join(
            f"{row['name']}\t{row['length']}\t0\t80\t81\n" for row in reference["contigs"]))
        paths["dictionary"].write_text("@HD\tVN:1.6\n" + "".join(
            f"@SQ\tSN:{row['name']}\tLN:{row['length']}\n" for row in reference["contigs"]))
        for name in ("bwa", "samtools"):
            paths[name].write_bytes(b"\0" * executor["executors"][name]["size_bytes"])
            paths[name].chmod(0o700)
        index_directory = directory / "index"
        index_directory.mkdir()
        prefix = index_directory / "reference"
        for suffix, expected in reference["index_files"].items():
            member = Path(str(prefix) + suffix)
            member.write_bytes(b"x" * expected["size_bytes"] if suffix == ".ann" else b"unit-index")
        if target_class == "index":
            target, pin, code = Path(str(prefix) + ".ann"), reference["index_files"][".ann"], "index_identity_mismatch"
        elif target_class == "executor":
            target, pin, code = paths["bwa"], executor["executors"]["bwa"], "executor_identity_mismatch"
        else:
            target, pin, code = paths[target_class], reference[target_class], "reference_identity_mismatch"
        resources = self.api.AlignmentResources(paths["fasta"], paths["fai"], paths["dictionary"], prefix,
                                                paths["bwa"], paths["samtools"], directory)
        real_verify = self.api._verify_file_identity
        real_sealed = self.api._sealed_copy
        real_resolve, real_read_text = Path.resolve, Path.read_text
        observed = []

        def verify(path, expected_sha, expected_size, deadline, failure_code):
            if Path(path) == target:
                observed.append((Path(path), expected_sha, expected_size, failure_code))
                return real_verify(path, expected_sha, expected_size, deadline, failure_code)
            # Other already-qualified identities are outside this one refusal.
            return {"sha256": expected_sha, "size_bytes": expected_size}

        def sealed(path, expected, deadline):
            if Path(path) == target:
                return real_sealed(path, expected, deadline)
            return os.open(os.devnull, os.O_RDONLY), {"sha256": expected["sha256"],
                                                      "size_bytes": expected["size_bytes"]}

        def resolve(path, *args, **kwargs):
            if str(path).startswith("/usr/lib/omnigenis-unit-runtime/"):
                return path
            return real_resolve(path, *args, **kwargs)

        def read_text(path, *args, **kwargs):
            if path == Path("/proc/cpuinfo"):
                return f"processor : {min(os.sched_getaffinity(0))}\nflags : sse4_1\n\n"
            return real_read_text(path, *args, **kwargs)

        def command(_profiles, workspace, label, argv, deadline, **kwargs):
            output = workspace / (label + ".stdout")
            if label in ("bwa-libraries", "samtools-libraries"):
                name = label.split("-", 1)[0]
                value = "".join(
                    f"{soname} => /usr/lib/omnigenis-unit-runtime/{soname} (0x1)\n"
                    for soname in executor["runtime_library_sets"][name])
            elif label == "bwa-version":
                value = "2.2.1\n"
            elif label == "samtools-version":
                value = ("samtools 1.25\nUsing htslib 1.25\n"
                         "curses=no libcurl=no S3=no GCS=no plugins=no "
                         "libdeflate=no lzma=no bzip2=no htscodecs=1.6.7\n")
            else:
                raise AssertionError("Unexpected native dispatch in identity refusal")
            output.write_text(value)
            return output

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.api, "_verify_file_identity", side_effect=verify))
            stack.enter_context(patch.object(self.api, "_sealed_copy", side_effect=sealed))
            stack.enter_context(patch.object(self.api, "_command", side_effect=command))
            stack.enter_context(patch.object(Path, "resolve", new=resolve))
            stack.enter_context(patch.object(Path, "read_text", new=read_text))
            stack.enter_context(patch.object(self.api.platform, "system", return_value=executor["platform"]["system"]))
            stack.enter_context(patch.object(self.api.platform, "machine", return_value=executor["platform"]["machine"]))
            native = stack.enter_context(patch.object(self.api, "_run_native",
                                       side_effect=AssertionError("Native alignment must not occur")))
            promotion = stack.enter_context(patch.object(self.api, "_promote",
                                          side_effect=AssertionError("Artifacts must not be promoted")))
            self.failure(code, lambda: self.api._observe_resources(
                resources, directory, time.monotonic() + 30, profiles))
            native.assert_not_called()
            promotion.assert_not_called()
        self.assertEqual(observed, [(target, pin["sha256"], pin["size_bytes"], code)])

    def identity_refusal(self, name, code):
        p = self.work / name
        p.write_bytes(b"independent synthetic identity bytes")
        before = p.read_bytes()
        self.failure(code, lambda: self.api._verify_file_identity(
            p, digest(b"different independently bound bytes"), len(before), time.monotonic() + 10, code,
        ))
        self.assertEqual(p.read_bytes(), before)

    def test_t07a_fasta_digest_mismatch_refuses(self):
        self.identity_refusal("reference.fna", "reference_identity_mismatch")
        self.resource_gate_refusal("fasta")

    def test_t07b_fai_digest_mismatch_refuses(self):
        self.identity_refusal("reference.fna.fai", "reference_identity_mismatch")
        self.resource_gate_refusal("fai")

    def test_t07c_dictionary_digest_mismatch_refuses(self):
        self.identity_refusal("reference.dict", "reference_identity_mismatch")
        self.resource_gate_refusal("dictionary")

    def test_t07d_native_index_digest_mismatch_refuses(self):
        self.identity_refusal("reference.ann", "index_identity_mismatch")
        self.resource_gate_refusal("index")

    def test_t08a_wrong_read_group_refuses(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        wrong = dict(RG, SM="synthetic:another-sample")
        paths, _ = self.vectors(record_vectors(r1, r2), read_group=wrong)
        with self.assertRaises(self.api._Failure) as caught:
            self.validate(snapshot, paths)
        self.assertIn(caught.exception.code, {"header_reference_mismatch", "malformed_output", "unsafe_read_group"})

    def test_t08b_wrong_reference_header_refuses(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        paths, _ = self.vectors(record_vectors(r1, r2), contigs=(("different_unit_ref", 50000),))
        self.failure("header_reference_mismatch", lambda: self.validate(snapshot, paths))

    def test_t09a_executor_pin_mismatch_refuses(self):
        self.identity_refusal("unexecuted-tool", "executor_identity_mismatch")
        self.resource_gate_refusal("executor")

    def test_t09b_owned_nonzero_exit_refuses(self):
        self.failure("native_execution_failed",
                     lambda: self.run_small("raise SystemExit(7)", self.small_limits()))
        self.assertTrue((self.work / "child.stderr").exists())

    def test_t10a_truncated_physical_bam_refuses(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        paths, _ = self.vectors(record_vectors(r1, r2))
        paths["bam_path"].write_bytes(paths["bam_path"].read_bytes()[:-len(BGZF_EOF) - 6])
        self.failure("truncated_output", lambda: self.validate(snapshot, paths))

    def test_t10b_middle_block_crc_refuses(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        paths, blocks = self.vectors(record_vectors(r1, r2))
        self.assertGreaterEqual(len(blocks), 3)
        _, _, offset, length = blocks[len(blocks) // 2]
        broken = bytearray(paths["bam_path"].read_bytes())
        broken[offset + length - 8] ^= 1
        paths["bam_path"].write_bytes(broken)
        self.failure("output_checksum_error", lambda: self.validate(snapshot, paths))

    def test_t11a_missing_primary_refuses(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        paths, _ = self.vectors(record_vectors(r1, r2)[1:])
        self.failure("primary_accounting_mismatch", lambda: self.validate(snapshot, paths))

    def test_t11b_extra_primary_refuses(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        records = record_vectors(r1, r2)
        records.append(copy.deepcopy(records[0]))
        paths, _ = self.vectors(records)
        self.failure("primary_accounting_mismatch", lambda: self.validate(snapshot, paths))

    def test_t12_duplicate_cluster_occurrences_are_preserved(self):
        r1, r2 = fixture_pair()
        r1 = r1.replace(b":1101:2:2 1:", b":1101:1:1 1:", 1)
        r2 = r2.replace(b":1101:2:2 2:", b":1101:1:1 2:", 1)
        snapshot = self.snapshot(r1, r2)
        first_qname = fastq_records(r1, 1)[0][0]
        self.assertEqual(sum(count for (qname, mate, _, _), count in snapshot.expected_reads.items()
                             if qname == first_qname and mate == 1), 2)
        paths, _ = self.vectors(record_vectors(r1, r2))
        accounting = self.validate(snapshot, paths)
        self.assertEqual(accounting["primary"], 256)
        self.assertTrue(accounting["orientation_restored_multiplicity_equal_FASTQ"])

        # CONSTRUCTED_TEST_ONLY: preserve original occurrence pairs, not just mate marginals.
        crossed = copy.deepcopy(record_vectors(r1, r2))
        for left, right in ((crossed[0], crossed[3]), (crossed[2], crossed[1])):
            left["next_pos"], right["next_pos"] = right["pos"], left["pos"]
            width = max(reference_end(left), reference_end(right)) - min(left["pos"], right["pos"])
            left["tlen"] = width if left["pos"] <= right["pos"] else -width
            right["tlen"] = -left["tlen"]
        crossed_paths, _ = self.vectors(crossed)
        self.failure("primary_accounting_mismatch", lambda: self.validate(snapshot, crossed_paths))
        colliding = record_vectors(r1, r2, [600, 600] + [600 + i * 311 for i in range(2, 128)])
        self.assertNotEqual(colliding[0]["seq"], colliding[2]["seq"])
        collision_paths, _ = self.vectors(colliding)
        self.assertEqual(self.validate(snapshot, collision_paths)["primary"], 256)
        one = b"\n".join(r1.split(b"\n")[:4]) + b"\n"
        two = b"\n".join(r2.split(b"\n")[:4]) + b"\n"
        identical_snapshot = self.snapshot(one * 2, two * 2)
        identical_paths, _ = self.vectors(record_vectors(one * 2, two * 2, [1000, 1000]))
        self.assertEqual(self.validate(identical_snapshot, identical_paths)["primary"], 4)

    def test_t13a_nonconstant_reverse_qualities_and_full_intake_length(self):
        r1, r2 = make_pair_at([1000], read_length=1000, eol=b"\r\n", include_n=True)
        snapshot = self.snapshot(r1, r2)
        records = record_vectors(r1, r2, [1000])
        original_quality = fastq_records(r2, 2)[0][3]
        self.assertNotEqual(original_quality, original_quality[::-1])
        self.assertEqual(records[1]["qual"], original_quality[::-1])
        paths, _ = self.vectors(records)
        self.assertTrue(self.validate(snapshot, paths)["orientation_restored_multiplicity_equal_FASTQ"])
        records[1]["qual"] = original_quality
        wrong, _ = self.vectors(records)
        self.failure("primary_accounting_mismatch", lambda: self.validate(snapshot, wrong))

        # CONSTRUCTED_TEST_ONLY: one-base Phred9, reverse mate and identical occurrence counts.
        one, two = make_pair_at([1200], read_length=1)
        def phred_nine(data):
            lines = data.splitlines()
            lines[3] = b"*"
            return b"\n".join(lines) + b"\n"
        one, two = phred_nine(one) * 2, phred_nine(two) * 2
        tiny_snapshot = self.snapshot(one, two)
        tiny_records = record_vectors(one, two, [1200, 1200])
        tiny_paths, _ = self.vectors(tiny_records)
        self.assertEqual(self.validate(tiny_snapshot, tiny_paths)["primary"], 4)
        missing_records = copy.deepcopy(tiny_records)
        for row in missing_records:
            row["qual"] = None
        converted_paths, _ = self.vectors(missing_records)
        sam_before = converted_paths["sam_path"].read_bytes()
        self.api._restore_sam_quality_sentinels(
            tiny_snapshot, contigs=CONTIGS, read_group=RG,
            sam_path=converted_paths["sam_path"], bam_path=converted_paths["bam_path"],
            deadline=time.monotonic() + 30,
        )
        self.assertEqual(converted_paths["sam_path"].read_bytes(), sam_before)
        self.assertEqual(gzip.decompress(converted_paths["bam_path"].read_bytes()),
                         gzip.decompress(tiny_paths["bam_path"].read_bytes()))
        reader = self.api._BgzfReader(converted_paths["bam_path"], time.monotonic() + 30)
        try:
            self.api._bam_header(reader, CONTIGS, RG)
            restored = []
            while (item := self.api._bam_record(reader, CONTIGS)) is not None:
                restored.append(item[0])
            reader.finish()
        finally:
            reader.close()
        self.assertEqual(len(restored), 4)
        self.assertTrue(all(record.quality == b"*" for record in restored))
        self.assertFalse((converted_paths["bam_path"].parent / "alignment-quality.tmp.bam").exists())
        # Missing values for a different source quality must never be substituted.
        plus_one, plus_two = one.replace(b"\n*\n", b"\n+\n"), two.replace(b"\n*\n", b"\n+\n")
        plus_snapshot = self.snapshot(plus_one, plus_two)
        untouched = converted_paths["bam_path"].read_bytes()
        self.failure("primary_accounting_mismatch", lambda: self.api._restore_sam_quality_sentinels(
            plus_snapshot, contigs=CONTIGS, read_group=RG,
            sam_path=converted_paths["sam_path"], bam_path=converted_paths["bam_path"],
            deadline=time.monotonic() + 30,
        ))
        self.assertEqual(converted_paths["bam_path"].read_bytes(), untouched)
        # An unexpected BAM value is not covered by native-SAM occurrence proof.
        tampered = copy.deepcopy(missing_records)
        tampered[0]["seq"] = b"C" if tampered[0]["seq"] != b"C" else b"A"
        tampered_paths, _ = self.vectors(tampered)
        tampered_paths["sam_path"].write_bytes(sam_before)
        before = tampered_paths["bam_path"].read_bytes()
        self.failure("primary_accounting_mismatch", lambda: self.api._restore_sam_quality_sentinels(
            tiny_snapshot, contigs=CONTIGS, read_group=RG,
            sam_path=tampered_paths["sam_path"], bam_path=tampered_paths["bam_path"],
            deadline=time.monotonic() + 30,
        ))
        self.assertEqual(tampered_paths["bam_path"].read_bytes(), before)
        self.assertFalse((tampered_paths["bam_path"].parent / "alignment-quality.tmp.bam").exists())

    def test_t13b_constructed_secondary_and_hard_clipped_supplementary(self):
        left, right = fixture_pair()
        r1 = b"\n".join(left.split(b"\n")[:4]) + b"\n"
        r2 = b"\n".join(right.split(b"\n")[:4]) + b"\n"
        snapshot = self.snapshot(r1, r2)
        records = record_vectors(r1, r2)
        secondary = copy.deepcopy(records[0])
        secondary["flag"] |= 256
        secondary["tags"].append(("ZZ", "B", ("i", [1, -2, 3])))
        supplementary = copy.deepcopy(records[1])
        supplementary["flag"] |= 2048
        supplementary["pos"] += 70
        supplementary["cigar"] = [(70, "H"), (80, "M")]
        supplementary["seq"] = supplementary["seq"][70:]
        supplementary["qual"] = None
        both = copy.deepcopy(secondary)
        both["flag"] |= 2048
        records = [records[0], secondary, both, records[1], supplementary]
        paths, _ = self.vectors(records)
        accounting = self.validate(snapshot, paths)
        self.assertEqual(accounting["primary"], 2)
        self.assertEqual(accounting["secondary"], 2)
        self.assertEqual(accounting["supplementary"], 2)
        self.assertEqual(accounting["secondary_and_supplementary"], 1)
        self.assertEqual(accounting["nonprimary_union"], 3)
        self.assertEqual(accounting["records"], 5)
        # Present SEQ still has to match when supplementary QUAL is missing.
        first = supplementary["seq"][0:1]
        supplementary["seq"] = (b"A" if first != b"A" else b"C") + supplementary["seq"][1:]
        malformed, _ = self.vectors(records)
        self.failure("secondary_supplementary_accounting_mismatch",
                     lambda: self.validate(snapshot, malformed))

        # CONSTRUCTED_TEST_ONLY: a longer reverse read can emit a one-base supplementary QUAL.
        supplementary["seq"] = records[3]["seq"][70:]
        original = fastq_records(r2, 2)[0]
        source_index = original[3].index(b"*")
        left_clip = len(original[2]) - 1 - source_index
        right_clip = len(original[2]) - left_clip - 1
        solitary = copy.deepcopy(records[3])
        solitary["flag"] |= 2048
        solitary["pos"] += 91
        solitary["cigar"] = ([(left_clip, "H")] if left_clip else []) + [(1, "M")]
        solitary["cigar"] += [(right_clip, "H")] if right_clip else []
        solitary["seq"] = solitary["seq"][left_clip:left_clip + 1]
        solitary["qual"] = b"*"
        clipped_records = records + [solitary]
        clipped_paths, _ = self.vectors(clipped_records)
        self.assertEqual(self.validate(snapshot, clipped_paths)["supplementary"], 3)
        converted = copy.deepcopy(clipped_records)
        converted[-1]["qual"] = None
        converted_paths, _ = self.vectors(converted)
        original_sam = converted_paths["sam_path"].read_bytes()
        self.api._restore_sam_quality_sentinels(
            snapshot, contigs=CONTIGS, read_group=RG, sam_path=converted_paths["sam_path"],
            bam_path=converted_paths["bam_path"], deadline=time.monotonic() + 30,
        )
        self.assertEqual(converted_paths["sam_path"].read_bytes(), original_sam)
        self.assertEqual(gzip.decompress(converted_paths["bam_path"].read_bytes()),
                         gzip.decompress(clipped_paths["bam_path"].read_bytes()))
        reader = self.api._BgzfReader(converted_paths["bam_path"], time.monotonic() + 30)
        try:
            self.api._bam_header(reader, CONTIGS, RG)
            decoded = []
            while (item := self.api._bam_record(reader, CONTIGS)) is not None:
                decoded.append(item[0])
            reader.finish()
        finally:
            reader.close()
        recovered = [row for row in decoded if row.flag & 2048 and row.sequence is not None
                     and len(row.sequence) == 1]
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].quality, b"*")
        omitted = [row for row in decoded if row.flag & 2048 and row.sequence is not None
                   and len(row.sequence) == 80]
        self.assertEqual(len(omitted), 1)
        self.assertIsNone(omitted[0].quality)

    def test_t14a_owned_child_memory_exhaustion_refuses(self):
        command = "import time;data=bytearray(96*1024*1024);print(len(data),flush=True);time.sleep(8)"
        self.failure("resource_limit_exceeded",
                     lambda: self.run_small(command, self.small_limits(rss=64 * 1024**2, wall=5)))
        transient = self.work / "transient-sort-files"
        transient.mkdir()
        (transient / "retained").write_bytes(b"abc")
        # os.walk may have listed a spill file that sort has already removed.
        with patch.object(self.api.os, "walk", return_value=[(str(transient), [], ["gone.tmp", "retained"])]):
            self.assertEqual(self.api._owned_size(transient), 3)
        (transient / "link").symlink_to(transient / "retained")
        self.failure("resource_limit_exceeded", lambda: self.api._owned_size(transient))

    def test_t14b_owned_child_deadline_kills_and_reaps(self):
        command = ("import os,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                   "print(os.getpid(),flush=True);os.write(1,b'x'*(512*1024));time.sleep(30)")
        samples = []
        original_group_usage = self.api._group_usage

        def record_group_usage(pgid):
            samples.append(time.monotonic())
            return original_group_usage(pgid)

        failures = []

        def run_in_calling_thread():
            try:
                self.run_small(command, self.small_limits(wall=1))
            except BaseException as error:
                failures.append(error)

        started = time.monotonic()
        caller = threading.Thread(target=run_in_calling_thread, daemon=True)
        with patch.object(self.api, "_group_usage", side_effect=record_group_usage), \
                patch.object(self.api.subprocess, "Popen", wraps=self.api.subprocess.Popen) as launches:
            caller.start()
            caller.join(8)
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], self.api._Failure)
        self.assertEqual(failures[0].code, "deadline_exceeded")
        self.assertEqual(launches.call_count, 1)
        self.assertNotIn("preexec_fn", launches.call_args.kwargs)
        self.assertLess(time.monotonic() - started, 8)
        self.assertGreaterEqual(len(samples), 2)
        # Reaping forces the final sample; pipe reads cannot force earlier ones.
        for before, after in zip(samples, samples[1:-1]):
            self.assertGreaterEqual(after - before, 0.020)
        pid = int((self.work / "child.stdout").read_text().splitlines()[0])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

        # Supervision must already be active before the bootstrap execs the
        # requested command, including when launch originates from a thread.
        bootstrap_work = self.work / "bootstrap-deadline"
        bootstrap_work.mkdir()
        delayed = ("import os,time\nprint(os.getpid(),flush=True)\ntime.sleep(30)\n"
                   + self.api._NATIVE_BOOTSTRAP)
        failures.clear()

        def run_delayed_bootstrap():
            try:
                self.api._run_native(
                    [sys.executable, "-I", "-B", "-c", "raise SystemExit(0)"],
                    cwd=bootstrap_work, deadline=time.monotonic() + 1, limits=self.small_limits(wall=1),
                    stdout_path=bootstrap_work / "startup.stdout", stderr_path=bootstrap_work / "startup.stderr")
            except BaseException as error:
                failures.append(error)

        caller = threading.Thread(target=run_delayed_bootstrap, daemon=True)
        with patch.object(self.api, "_NATIVE_BOOTSTRAP", delayed):
            caller.start()
            caller.join(8)
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], self.api._Failure)
        self.assertEqual(failures[0].code, "deadline_exceeded")
        startup_pid = int((bootstrap_work / "startup.stdout").read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(startup_pid, 0)
        startup_receipt = json.loads((bootstrap_work / "startup.stdout.receipt.json").read_text())
        self.assertEqual(startup_receipt["status"], "FAIL")
        self.assertEqual(startup_receipt["diagnostic"], "deadline_exceeded")

    def test_t15_bai_accounting_and_indexed_stream_must_match(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        paths, _ = self.vectors(record_vectors(r1, r2))
        correct_bai = paths["bai_path"].read_bytes()
        wrong = correct_bai[:-8] + struct.pack("<Q", 1)
        paths["bai_path"].write_bytes(wrong)
        self.failure("index_validation_failed", lambda: self.validate(snapshot, paths))
        paths["bai_path"].write_bytes(correct_bai)
        lines = paths["indexed_path"].read_bytes().splitlines(keepends=True)
        first_record = next(i for i, line in enumerate(lines) if not line.startswith(b"@"))
        paths["indexed_path"].write_bytes(b"".join(lines[:first_record] + lines[first_record + 1:]))
        self.failure("index_validation_failed", lambda: self.validate(snapshot, paths))

        # A chunk in an intersecting leaf is insufficient for a record spanning
        # both sides of that leaf: a narrow query on the other side would miss it.
        cross_r1, cross_r2 = make_pair_at([16300])
        crossing = self.snapshot(cross_r1, cross_r2)
        crossing_paths, _ = self.vectors(record_vectors(cross_r1, cross_r2, [16300]))
        self.assertEqual(self.validate(crossing, crossing_paths)["primary"], 2)
        wrong_leaf = bytearray(crossing_paths["bai_path"].read_bytes())
        self.assertEqual(struct.unpack_from("<I", wrong_leaf, 12)[0], 585)
        struct.pack_into("<I", wrong_leaf, 12, 4681)
        crossing_paths["bai_path"].write_bytes(wrong_leaf)
        self.failure("index_validation_failed", lambda: self.validate(crossing, crossing_paths))

    def test_t16_atomic_schema_refusal_determinism_and_invariance(self):
        r1, r2 = fixture_pair()
        snapshot = self.snapshot(r1, r2)
        records = record_vectors(r1, r2)
        paths, _ = self.vectors(records)
        before = {key: digest(path.read_bytes()) for key, path in paths.items()}
        accounting = self.validate(snapshot, paths)
        accepted = constructed_accepted(snapshot, r1, r2, paths, accounting)
        bam, bai = paths["bam_path"].read_bytes(), paths["bai_path"].read_bytes()
        first = self.api._promote(bam, bai, copy.deepcopy(accepted))
        second = self.api._promote(bam, bai, copy.deepcopy(accepted))
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.artifacts, second.artifacts)
        invalid_parent = first.to_dict()
        accepted_export = invalid_parent["canonical_payload"]["accepted_alignment"]
        accepted_export["bai_artifact"]["parent_artifact_ids"] = [accepted_export["bai_artifact"]["artifact_id"]]
        duplicate_kind = first.to_dict()
        duplicate_kind["raw_artifact_refs"][1] = "bam:sha256:" + "0" * 64
        reversed_refs = first.to_dict()
        reversed_refs["raw_artifact_refs"].reverse()
        wrong_roles = first.to_dict()
        wrong_roles["canonical_payload"]["accepted_alignment"]["bam_artifact"]["parent_artifact_ids"] = [
            "bai:sha256:" + format(index, "064x") for index in range(6)]
        reversed_parents = first.to_dict()
        parent_ids = reversed_parents["canonical_payload"]["accepted_alignment"]["bam_artifact"]["parent_artifact_ids"]
        parent_ids[0], parent_ids[1] = parent_ids[1], parent_ids[0]
        for invalid_export in (invalid_parent, duplicate_kind, reversed_refs, wrong_roles, reversed_parents):
            self.failure("output_schema_failed", lambda value=invalid_export: self.api._validate_export(value))
        for binding, field in (("executor_binding", "profile_sha256"),
                               ("executor_binding", "runtime_libraries_sha256"),
                               ("reference_binding", "profile_sha256"),
                               ("reference_binding", "contig_dictionary_sha256")):
            with self.subTest(binding=binding, field=field):
                invalid_digest = first.to_dict()
                invalid_digest["canonical_payload"]["accepted_alignment"][binding][field] = "0" * 64
                self.failure("output_schema_failed",
                             lambda value=invalid_digest: self.api._validate_export(value))
        self.api._validate_export(self.api._refusal("promotion_failed").to_dict())
        wrong_parent = copy.deepcopy(accepted)
        wrong_parent["bai_artifact"]["parent_artifact_ids"] = ["bam:sha256:" + "0" * 64]
        self.failure("promotion_failed", lambda: self.api._promote(bam, bai, wrong_parent))
        with patch.object(self.api, "_validate_export", side_effect=self.api._Failure("output_schema_failed")):
            self.failure("output_schema_failed", lambda: self.api._promote(bam, bai, copy.deepcopy(accepted)))
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(before, {key: digest(path.read_bytes()) for key, path in paths.items()})
        self.api._verify_snapshot(snapshot, time.monotonic() + 10)
        normalized = []
        for ordinal, command in enumerate(("bwa-mem2 mem example-reference-a example-reads",
                                           "bwa-mem2 mem example-reference-b example-reads")):
            source = self.work / ("native-" + str(ordinal) + ".sam")
            destination = self.work / ("normalized-" + str(ordinal) + ".sam")
            original = header(coordinate=False, command=command) + b"".join(sam_record(row) for row in records)
            source.write_bytes(original)
            self.api._normalize_sam_header(source, destination, contigs=CONTIGS, read_group=RG,
                                           deadline=time.monotonic() + 10)
            self.assertEqual(source.read_bytes(), original)
            normalized.append(destination.read_bytes())
            self.assertNotIn(b"\tCL:", destination.read_bytes())
        self.assertEqual(normalized[0], normalized[1])
        self.assertEqual(
            [line for line in normalized[0].splitlines() if not line.startswith(b"@")],
            [sam_record(row).rstrip(b"\n") for row in records],
        )

        # Constructed command outputs isolate the promotion boundary. They are
        # unit controls, not execution of the published reference/executor.
        calls, control_workspaces = [], []
        native_streams = {key: path.read_bytes() for key, path in paths.items()}
        native_sam_bytes = header(coordinate=False) + b"".join(sam_record(row) for row in records)

        def observed_controls(resources, workspace, deadline, profiles):
            control_workspaces.append(workspace)
            index_prefix = workspace / "unit-unused-index"
            expected_index_files = {}
            for suffix in self.api.INDEX_SUFFIXES:
                data = b"constructed-index-member:" + suffix.encode("ascii")
                member = Path(str(index_prefix) + suffix)
                member.write_bytes(data)
                member.chmod(0o400)
                expected_index_files[suffix] = {"sha256": digest(data), "size_bytes": len(data)}
            index_view = self.api._bind_index_view(index_prefix, workspace, expected_index_files, deadline)
            descriptors = (os.memfd_create("unit-unused-bwa"), os.memfd_create("unit-unused-samtools"))
            return self.api._ObservedResources(
                CONTIGS, copy.deepcopy(accepted["reference_binding"]),
                copy.deepcopy(accepted["executor_binding"]), (), index_prefix, descriptors, index_view)

        def materialize_controls(profiles, workspace, label, argv, deadline, kind="control",
                                 stdout_path=None, **kwargs):
            calls.append(label)
            output = stdout_path or workspace / (label + ".stdout")
            if label == "alignment":
                output.write_bytes(native_sam_bytes)
            elif label == "sort":
                (workspace / "alignment.bam").write_bytes(native_streams["bam_path"])
                output.write_bytes(b"")
            elif label == "index":
                (workspace / "alignment.bam.bai").write_bytes(native_streams["bai_path"])
                output.write_bytes(b"")
            elif label == "decode":
                output.write_bytes(native_streams["decoded_path"])
            elif label == "indexed":
                output.write_bytes(native_streams["indexed_path"])
            else:
                raise AssertionError("Unexpected unit command boundary")
            return output

        def replace_after_validation(observed, deadline):
            promoted_path = observed.index_prefix.parent / "alignment.bam"
            original = promoted_path.read_bytes()
            promoted_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.api, "_observe_resources", side_effect=observed_controls))
            stack.enter_context(patch.object(self.api, "_command", side_effect=materialize_controls))
            stack.enter_context(patch.object(self.api, "_capacity", return_value=None))
            stack.enter_context(patch.object(self.api, "_verify_resources", side_effect=replace_after_validation))
            stack.enter_context(patch.object(self.api, "_run_native",
                                            side_effect=AssertionError("No native unit execution")))
            promotion = stack.enter_context(patch.object(
                self.api, "_promote", side_effect=AssertionError("Replaced output must not be promoted")))
            replaced = self.api.align_paired_fastq(
                r1, r2, bound_profile(r1, r2), execution_profile=execution_profile(),
                resources=self.unused_resources())
        self.assertEqual(calls, ["alignment", "sort", "index", "decode", "indexed"])
        promotion.assert_not_called()
        self.assertFalse(replaced.is_valid)
        self.assertEqual(replaced.artifacts, ())
        self.assertEqual(replaced.to_dict()["canonical_payload"]["diagnostics"],
                         [{"code": "promotion_failed"}])

        # Terminal I/O and the final size measurement are inside the acceptance
        # deadline. A late refusal must replace both private success documents.
        original_save = self.api._save_terminal
        original_owned_size = self.api._owned_size
        for refusal_code in (None, "resource_limit_exceeded", "deadline_exceeded"):
            clock = [time.monotonic()]

            def delayed_terminal(workspace, result, started, **kwargs):
                original_save(workspace, result, started, **kwargs)
                if result.is_valid:
                    clock[0] += 1801

            def oversized_terminal(workspace):
                if (workspace / "result.json").exists():
                    return self.api.MAX_MUTABLE_BYTES + 1
                return original_owned_size(workspace)

            with self.subTest(finalization=refusal_code), ExitStack() as stack:
                stack.enter_context(patch.object(self.api, "_observe_resources", side_effect=observed_controls))
                stack.enter_context(patch.object(self.api, "_command", side_effect=materialize_controls))
                stack.enter_context(patch.object(self.api, "_capacity", return_value=None))
                stack.enter_context(patch.object(self.api, "_verify_resources", return_value=None))
                stack.enter_context(patch.object(self.api, "_run_native",
                                                side_effect=AssertionError("No native unit execution")))
                if refusal_code == "resource_limit_exceeded":
                    stack.enter_context(patch.object(self.api, "_owned_size", side_effect=oversized_terminal))
                elif refusal_code == "deadline_exceeded":
                    stack.enter_context(patch.object(self.api.time, "monotonic", side_effect=lambda: clock[0]))
                    stack.enter_context(patch.object(self.api, "_save_terminal", side_effect=delayed_terminal))
                finalized = self.api.align_paired_fastq(
                    r1, r2, bound_profile(r1, r2), execution_profile=execution_profile(),
                    resources=self.unused_resources())
            workspace = control_workspaces[-1]
            exported = finalized.to_dict()
            self.assertEqual(json.loads((workspace / "result.json").read_text()), exported)
            terminal = json.loads((workspace / "terminal.json").read_text())
            self.assertEqual(terminal["status"], exported["canonical_status"])
            self.assertEqual(terminal["result_sha256"], digest(finalized._canonical_json))
            self.assertEqual(terminal["artifact_ids"], [item.artifact_id for item in finalized.artifacts])
            self.assertFalse(list(workspace.glob("*.replacement")))
            if refusal_code is None:
                self.assertTrue(finalized.is_valid)
                self.assertEqual(finalized.artifacts, first.artifacts)
                self.assertFalse((workspace / "promotion-refusal.json").exists())
            else:
                self.assertFalse(finalized.is_valid)
                self.assertEqual(finalized.artifacts, ())
                self.assertEqual(exported["canonical_payload"]["diagnostics"], [{"code": refusal_code}])
                self.assertEqual(json.loads((workspace / "promotion-refusal.json").read_text()), exported)

        # A failed terminal write and failed recovery log must not retain VALID.
        original_private_json = self.api._private_json
        write_failures = {"terminal": 0, "recovery": 0}

        def fail_terminal_and_recovery(path, value, **kwargs):
            if path.name == "terminal.json" and value.get("status") == "VALID":
                write_failures["terminal"] += 1
                raise OSError("Constructed terminal write failure")
            if path.name == "promotion-refusal.json":
                write_failures["recovery"] += 1
                raise OSError("Constructed recovery write failure")
            return original_private_json(path, value, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.api, "_observe_resources", side_effect=observed_controls))
            stack.enter_context(patch.object(self.api, "_command", side_effect=materialize_controls))
            stack.enter_context(patch.object(self.api, "_capacity", return_value=None))
            stack.enter_context(patch.object(self.api, "_verify_resources", return_value=None))
            stack.enter_context(patch.object(self.api, "_run_native",
                                            side_effect=AssertionError("No native unit execution")))
            stack.enter_context(patch.object(self.api, "_private_json", side_effect=fail_terminal_and_recovery))
            refused = self.api.align_paired_fastq(
                r1, r2, bound_profile(r1, r2), execution_profile=execution_profile(),
                resources=self.unused_resources())
        self.assertEqual(write_failures, {"terminal": 1, "recovery": 1})
        self.assertFalse(refused.is_valid)
        self.assertEqual(refused.artifacts, ())
        workspace = control_workspaces[-1]
        self.assertFalse(list(workspace.glob("*.replacement")))
        for document in workspace.glob("*.json"):
            value = json.loads(document.read_text())
            self.assertNotEqual(value.get("canonical_status"), "VALID", document.name)
            self.assertNotEqual(value.get("status"), "VALID", document.name)


if __name__ == "__main__":
    unittest.main()
