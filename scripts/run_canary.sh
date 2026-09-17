#!/usr/bin/env bash
set -euo pipefail

readonly PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
readonly OUTPUT_DIR=${1:-"$PROJECT_ROOT/results/canary"}
readonly WORK_DIR="$OUTPUT_DIR/work"

if [[ -s "$OUTPUT_DIR/report.json" ]] && jq -e '.status == "PASS"' "$OUTPUT_DIR/report.json" >/dev/null; then
  cat "$OUTPUT_DIR/report.json"
  exit 0
fi

mkdir -p "$OUTPUT_DIR" "$WORK_DIR"
if [[ "${CANARY_VERSION_POLICY:-PINNED}" == "PINNED" ]]; then
  "$PROJECT_ROOT/scripts/check_versions.sh" > "$OUTPUT_DIR/tool_versions.tsv"
else
  {
    printf 'tool\tversion\n'
    printf 'java\t%s\n' "$(java -version 2>&1 | head -1)"
    printf 'samtools\t%s\n' "$(samtools --version | head -1)"
    printf 'bcftools\t%s\n' "$(bcftools --version | head -1)"
    printf 'bwa-mem2\t%s\n' "$(bwa-mem2 version 2>&1 | head -1 || true)"
    printf 'gatk\t%s\n' "$(gatk --version 2>&1 | tail -1)"
    printf 'nextflow\t%s\n' "$(nextflow -version 2>&1 | grep -m1 version || true)"
    printf 'snakemake\t%s\n' "$(snakemake --version 2>&1 | head -1)"
    printf 'pypdfium2\t%s\n' "$(python3 -c 'import importlib.metadata as md; print(md.version("pypdfium2"))')"
  } > "$OUTPUT_DIR/tool_versions.tsv"
fi
if command -v micromamba >/dev/null 2>&1; then
  micromamba list --name base --explicit > "$OUTPUT_DIR/conda-explicit.lock.txt"
  micromamba list --name base --json > "$OUTPUT_DIR/conda-inventory.json"
fi
# Functional editorial runtime canary is part of the same candidate witness.
# This makes session promotion contingent on the PDF/DOCX renderer stack, not only NGS executables.
python3 - <<'PY' "$WORK_DIR/editorial-canary.pdf" "$WORK_DIR/editorial-canary.png" "$OUTPUT_DIR/editorial-runtime.json"
import importlib.metadata as md
import json, sys
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

pdf, png, out = sys.argv[1:4]
c = canvas.Canvas(pdf, pagesize=A4)
c.drawString(72, 760, "GENOMA editorial runtime canary")
c.save()

document = pdfium.PdfDocument(pdf)
try:
    if len(document) != 1:
        raise RuntimeError(f"editorial canary page count mismatch: {len(document)}")
    page = document[0]
    try:
        bitmap = page.render(scale=2.0, rotation=0, rev_byteorder=True)
        try:
            image = bitmap.to_pil().convert("RGB")
            image.save(png, format="PNG", optimize=True)
            image.close()
        finally:
            bitmap.close()
    finally:
        page.close()
finally:
    document.close()

payload = {
    "status": "PASS",
    "renderer": "PDFium",
    "render_dpi": 144,
    "python_packages": {
        "reportlab": md.version("reportlab"),
        "python-docx": md.version("python-docx"),
        "pypdf": md.version("pypdf"),
        "Pillow": md.version("Pillow"),
        "pypdfium2": md.version("pypdfium2"),
    },
    "functional_outputs": ["PNG"],
}
open(out, "w", encoding="utf-8").write(json.dumps(payload, indent=2) + "\n")
PY
if [[ ! -s "$WORK_DIR/editorial-canary.png" ]]; then
  printf 'FAIL\teditor_runtime\treason=empty_pdfium_png_raster\n' >&2
  exit 7
fi

python3 "$PROJECT_ROOT/scripts/generate_canary.py" "$WORK_DIR/input"

reference="$WORK_DIR/input/reference.fa"
r1="$WORK_DIR/input/reads_R1.fastq.gz"
r2="$WORK_DIR/input/reads_R2.fastq.gz"
truth="$WORK_DIR/input/truth.vcf"
bam="$WORK_DIR/CANARY.sorted.bam"

samtools faidx "$reference"
gatk --java-options "-Xmx2g" CreateSequenceDictionary \
  --REFERENCE "$reference" \
  --OUTPUT "$WORK_DIR/input/reference.dict"
bwa-mem2 index "$reference"

read_group=$'@RG\tID:CANARY\tSM:CANARY\tPL:ILLUMINA\tLB:SYNTHETIC\tPU:UNIT1'
bwa-mem2 mem -t "${CANARY_THREADS:-2}" -R "$read_group" "$reference" "$r1" "$r2" \
  | samtools sort --threads "${CANARY_THREADS:-2}" --output-fmt BAM -o "$bam" -
samtools index "$bam"
samtools quickcheck -v "$bam"
samtools flagstat "$bam" > "$OUTPUT_DIR/flagstat.txt"

bgzip --stdout "$truth" > "$WORK_DIR/truth.vcf.gz"
tabix --preset vcf "$WORK_DIR/truth.vcf.gz"
bcftools norm --fasta-ref "$reference" --multiallelics -any --output-type z \
  --output "$WORK_DIR/truth.norm.vcf.gz" "$WORK_DIR/truth.vcf.gz"
tabix --preset vcf "$WORK_DIR/truth.norm.vcf.gz"

bcftools mpileup --threads "${CANARY_THREADS:-2}" --output-type u \
  --fasta-ref "$reference" --annotate FORMAT/DP,FORMAT/AD "$bam" \
  | bcftools call --multiallelic-caller --variants-only --output-type z \
      --output "$WORK_DIR/bcftools.raw.vcf.gz"
tabix --preset vcf "$WORK_DIR/bcftools.raw.vcf.gz"
bcftools norm --fasta-ref "$reference" --multiallelics -any --output-type z \
  --output "$WORK_DIR/bcftools.norm.vcf.gz" "$WORK_DIR/bcftools.raw.vcf.gz"
tabix --preset vcf "$WORK_DIR/bcftools.norm.vcf.gz"

gatk --java-options "-Xmx2g" HaplotypeCaller \
  --reference "$reference" \
  --input "$bam" \
  --output "$WORK_DIR/gatk.raw.vcf.gz" \
  --intervals chrSynthetic \
  --native-pair-hmm-threads "${CANARY_THREADS:-2}"
bcftools norm --fasta-ref "$reference" --multiallelics -any --output-type z \
  --output "$WORK_DIR/gatk.norm.vcf.gz" "$WORK_DIR/gatk.raw.vcf.gz"
tabix --force --preset vcf "$WORK_DIR/gatk.norm.vcf.gz"

python3 "$PROJECT_ROOT/scripts/score_variants.py" \
  "$WORK_DIR/truth.norm.vcf.gz" "$WORK_DIR/bcftools.norm.vcf.gz" \
  --output "$OUTPUT_DIR/bcftools.score.json" --require-perfect
python3 "$PROJECT_ROOT/scripts/score_variants.py" \
  "$WORK_DIR/truth.norm.vcf.gz" "$WORK_DIR/gatk.norm.vcf.gz" \
  --output "$OUTPUT_DIR/gatk.score.json" --require-perfect

jq --null-input \
  --slurpfile fixture "$WORK_DIR/input/fixture.json" \
  --slurpfile bcftools "$OUTPUT_DIR/bcftools.score.json" \
  --slurpfile gatk "$OUTPUT_DIR/gatk.score.json" \
  --slurpfile editorial "$OUTPUT_DIR/editorial-runtime.json" \
  --arg version_policy "${CANARY_VERSION_POLICY:-PINNED}" \
  '{
    status: "PASS",
    classification: "synthetic functional canary; not clinical validation",
    sensitive_data: false,
    version_policy: $version_policy,
    fixture: $fixture[0],
    callers: {bcftools: $bcftools[0], gatk_haplotypecaller: $gatk[0]},
    editorial_runtime: $editorial[0],
    limitations: [
      "three synthetic SNPs only",
      "no difficult regions, indels, CNV, SV, repeats, contamination, BQSR or WGS benchmark",
      "does not replace GIAB or laboratory analytical validation"
    ]
  }' > "$OUTPUT_DIR/report.json"

cat "$OUTPUT_DIR/report.json"
