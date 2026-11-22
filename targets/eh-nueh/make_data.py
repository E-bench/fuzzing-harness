#!/usr/bin/env python3
"""Seed corpora and base cases of the eh-nueh target.

  make_data.py <dfz_tool> <data dir> <seeds dir>     (DFZ_CFG must point at the target's harness.cfg)

  <seeds>/locus/    hand-made inputs of the structured surface: one per structure template, catalog field, read
                    container, option, unusual record and malformed-catalog variant (byte layout: adapter.cpp)
  <data>/base/<n>/  decoded base cases (ref.fa, ref.fa.fai, catalog.json, reads.bam) for the raw surfaces
  <seeds>/catalog/, <seeds>/sam/, <seeds>/bam/   the catalogs / reads of the locus seeds, as inputs of the raw surfaces
"""
import gzip, os, shutil, subprocess, sys

H = {k: i for i, k in enumerate("CONTAINER OPTIONS LAYOUT READLEN SEED DEPTH ERRORS NUOPT NOISE UNMAPPED HEADER EXT QUAL FRAG NU2 RES".split())}
L = {k: i for i, k in enumerate("TEMPLATE MOTIF REF1 REF2 TYPE FIELDS PLACE HAP1 HAP2 HAPX IMPURE SMALL COVER IRR MALFORM MISC".split())}
NTEMPL, NBASE = 24, 6


def case(hdr=None, loci=None, extras=None):
    h = bytearray(16)
    for k, v in (hdr or {}).items():
        h[H[k]] = v
    loci = loci or [{}]
    h[H["LAYOUT"]] = (h[H["LAYOUT"]] & ~3) | (len(loci) - 1)
    out = bytes(h)
    for l in loci:
        b = bytearray(16)
        for k, v in l.items():
            b[L[k]] = v
        out += bytes(b)
    for e in extras or []:
        out += bytes(e + [0] * (8 - len(e)))
    return out


def locus_seeds():
    s = {}
    s["default"] = case()
    s["het_expanded"] = case(loci=[{"HAP2": 0x80 | 17}])              # 10 / 35 copies
    s["hom_long"] = case(loci=[{"HAP1": 0x80 | 22, "HAP2": 0x80 | 22}])   # 100 / 100
    s["rare_irr"] = case(loci=[{"TYPE": 1 | (2 << 4), "HAP2": 0x80 | 25, "IRR": 4}])   # RareRepeat, off-targets, 200 copies, IRR pairs
    s["rare_irr_anchor"] = case(loci=[{"TYPE": 1 | (1 << 4), "HAP2": 0x80 | 27, "IRR": 3 | 0x80}])
    s["rare_no_offtarget"] = case(loci=[{"TYPE": 1, "HAP2": 0x80 | 24, "IRR": 3 | 0x20}])
    for t in range(NTEMPL):
        s[f"template{t:02d}"] = case(loci=[{"TEMPLATE": t, "HAP2": 1, "MISC": 4}])
        s[f"template{t:02d}_rare"] = case(loci=[{"TEMPLATE": t, "TYPE": 1 | 8, "HAP1": 3, "HAP2": 0x80 | 14, "MISC": 0x28}])
    for m in range(0, 32, 1):
        s[f"motif{m:02d}"] = case(loci=[{"MOTIF": m, "HAP2": 5}])
    s["chrX_male"] = case(hdr={"OPTIONS": 1}, loci=[{"PLACE": 1, "HAP2": 3}])
    s["chrX_female"] = case(loci=[{"PLACE": 1, "HAP2": 3}])
    s["chrY_male"] = case(hdr={"OPTIONS": 1}, loci=[{"PLACE": 2, "HAP2": 3}])
    s["chrY_female"] = case(loci=[{"PLACE": 2}])
    s["nochr_X_male"] = case(hdr={"OPTIONS": 1, "LAYOUT": 0x40}, loci=[{"PLACE": 1}])
    for c in range(1, 4):
        s[f"container{c}"] = case(hdr={"CONTAINER": c}, loci=[{"HAP2": 3}])
    for b in range(1, 4):
        s[f"smallblocks{b}"] = case(hdr={"CONTAINER": 1 | (b << 6)}, loci=[{"HAP2": 3}])
    s["noeof"] = case(hdr={"CONTAINER": 4})
    for o in range(1, 4):
        s[f"order{o}"] = case(hdr={"CONTAINER": o << 4}, loci=[{"HAP2": 3}])
    s["path_aligner"] = case(hdr={"OPTIONS": 2}, loci=[{"HAP2": 0x80 | 16}])
    for t in range(1, 4):
        s[f"threads{t}"] = case(hdr={"OPTIONS": t << 2}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}, {"TEMPLATE": 3}])
    for e in range(1, 16):
        s[f"ext{e:02d}"] = case(hdr={"EXT": e}, loci=[{"HAP2": 3}])
    s["nobamlet"] = case(hdr={"OPTIONS": 0x40})
    s["loglevel"] = case(hdr={"OPTIONS": 0x30})
    for r in range(1, 16):
        s[f"readlen{r:02d}"] = case(hdr={"READLEN": r, "FRAG": r}, loci=[{"HAP2": 0x80 | 15}])
    for d in range(1, 16):
        s[f"depth{d:02d}"] = case(hdr={"DEPTH": d}, loci=[{"HAP2": 3}])
    for e in (1, 2, 3, 4, 5, 6, 7, 8, 16, 32, 0x3B):
        s[f"errors{e:02x}"] = case(hdr={"ERRORS": e}, loci=[{"HAP2": 3}])
    for q in range(1, 8):
        s[f"qual{q}"] = case(hdr={"QUAL": q | (q << 3)}, loci=[{"HAP2": 3}])
    s["noise_unmapped"] = case(hdr={"NOISE": 6, "UNMAPPED": 6})
    s["unmapped_inside"] = case(hdr={"UNMAPPED": 0x14})
    for h in range(1, 10):
        s[f"header{h}"] = case(hdr={"HEADER": h}, loci=[{"TYPE": 1 | (1 << 4), "IRR": 2}])
    s["fasta_one_line"] = case(hdr={"HEADER": 0x10})
    s["catalog_empty"] = case(hdr={"HEADER": 1 << 5})
    s["catalog_object"] = case(hdr={"HEADER": 2 << 5})
    # several loci: separate contigs, one contig at every spacing
    s["loci4"] = case(loci=[{"HAP2": 3}, {"TEMPLATE": 2, "MOTIF": 1}, {"TEMPLATE": 3}, {"TEMPLATE": 4}])
    for sp in range(8):
        s[f"onecontig_spacing{sp}"] = case(hdr={"LAYOUT": 4 | (sp << 3)}, loci=[{"HAP2": 3}, {"MOTIF": 2, "HAP2": 1}, {"TEMPLATE": 3}])
    for f in range(1, 16):
        s[f"leftflank{f:02d}"] = case(loci=[{"PLACE": f << 2, "HAP2": 3}])
        s[f"rightflank{f:02d}"] = case(loci=[{"FIELDS": f << 4, "HAP2": 3}])
    for st in range(1, 4):
        s[f"flankstyle{st}"] = case(loci=[{"PLACE": st << 6, "HAP2": 3}])
    for f in range(1, 16):
        s[f"fields{f:02d}"] = case(loci=[{"FIELDS": f, "HAP2": 3, "MISC": f * 9 % 64}])
    for m in range(8):
        s[f"mincov{m}"] = case(loci=[{"MALFORM": 0x80 | ((m & 3) << 5), "COVER": 4 if m < 4 else 0}])
    for t in range(1, 4):
        s[f"type{t}"] = case(loci=[{"TYPE": t, "HAP2": 3}])
    s["type_array"] = case(loci=[{"TYPE": 4 | 8, "HAP2": 3}])
    s["swap_smn"] = case(loci=[{"TEMPLATE": 3, "TYPE": 0x40, "MISC": 4}])
    s["swap_repeat"] = case(loci=[{"TEMPLATE": 3, "TYPE": 0x80}])
    for sm in range(8):
        s[f"swap{sm}"] = case(loci=[{"TEMPLATE": 3, "SMALL": sm, "MISC": 8}])
        s[f"optional{sm}"] = case(loci=[{"TEMPLATE": 4, "SMALL": sm, "MISC": 0x20}])
        s[f"literal{sm}"] = case(loci=[{"TEMPLATE": 2, "SMALL": sm << 4, "HAP2": 3}])
    for m in range(4):
        s[f"smallvar_ref{m}"] = case(loci=[{"TEMPLATE": 5, "MISC": m | 0x10 * (m & 1)}])
    for c in range(32):
        s[f"hapdelta{c:02d}"] = case(loci=[{"HAP1": c, "HAP2": (c * 7) % 32}])
        s[f"hapabs{c:02d}"] = case(loci=[{"HAP1": 0x80 | c, "HAP2": 0x80 | ((c * 5) % 32), "TYPE": 1 | (1 << 4), "IRR": 2}])
    for r in (1, 2, 3, 5, 20, 30, 47):
        s[f"ref{r:02d}"] = case(loci=[{"REF1": r, "HAP2": 1}])
    s["haploid_sample"] = case(loci=[{"COVER": 0x80}])
    s["impure"] = case(loci=[{"IMPURE": 30, "HAP1": 0x80 | 18, "HAP2": 0x80 | 20}])
    for k in range(1, 24):
        s[f"extra{k:02d}"] = case(loci=[{"HAP2": 3}], extras=[[k, 0, 60, 0, 0, 0, 5], [k, 0, 20, 2, 3, 9, 40]])
    for c in range(1, 8):
        s[f"extra_content{c}"] = case(extras=[[4, 0, 60, 0, c], [11, 0, 30, 1, c]])
    for m in range(1, 31):
        s[f"malform{m:02d}"] = case(loci=[{"MALFORM": m}])
        s[f"malform{m:02d}_second"] = case(loci=[{}, {"MALFORM": m, "TEMPLATE": 2}])
    # options that only the rewrite has
    for u in (1, 2, 3, 4, 5, 8, 16, 0x40, 0x80, 0xC0, 0x1B):
        s[f"nuopt{u:02x}"] = case(hdr={"NUOPT": u}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}])
    for ix in range(1, 4):                # an index next to the reads, with the reader that uses it (threads >= 4)
        s[f"index{ix}"] = case(hdr={"RES": ix, "OPTIONS": 2 << 2}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}])
        s[f"index{ix}_blocks"] = case(hdr={"RES": ix, "OPTIONS": 3 << 2, "CONTAINER": 1 | (1 << 6), "NUOPT": 3}, loci=[{"HAP2": 3}])
    for x in range(1, 16):                # extra loci without reads: catalog sizes around the rewrite's batch limits
        s[f"extraloci{x:02d}"] = case(hdr={"NOISE": x << 4, "OPTIONS": (x % 4) << 2}, loci=[{"HAP2": 3}])
    s["threads16"] = case(hdr={"OPTIONS": 3 << 2, "RES": 0x40}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}])
    for cli in range(1, 16):              # command-line errors
        s[f"cli{cli:02d}"] = case(hdr={"RES": cli << 2}, loci=[{"HAP2": 3}])
    # ---- seeds for single branches named by the coverage analysis (docs/GENERATORS.md, the loop)
    T4, T8 = 2 << 2, 3 << 2                                   # --threads 4, 8 (the rewrite's parallel reader)
    for k in (1, 2, 3, 5, 7, 10, 16, 22):                     # unusual records under the parallel reader
        s[f"par_extra{k:02d}"] = case(hdr={"OPTIONS": T4}, loci=[{"HAP2": 3}], extras=[[k, 0, 60, 0, 0, 0, 5], [k, 0, 20, 2, 3, 9, 40]])
    s["par_unmapped_end"] = case(hdr={"OPTIONS": T4, "UNMAPPED": 6}, loci=[{"HAP2": 3}])
    s["par_noise"] = case(hdr={"OPTIONS": T8, "NOISE": 9}, loci=[{"HAP2": 3}])
    for b in range(1, 4):
        s[f"par_blocks{b}"] = case(hdr={"OPTIONS": T4, "CONTAINER": 1 | (b << 6)}, loci=[{"HAP2": 3}])
    s["par_sam"] = case(hdr={"OPTIONS": T4, "CONTAINER": 2}, loci=[{"HAP2": 3}])
    s["par_stored_index"] = case(hdr={"OPTIONS": T4, "CONTAINER": 3, "RES": 1, "READLEN": 11, "DEPTH": 2}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}])
    s["par_many_pairs"] = case(hdr={"OPTIONS": T4, "DEPTH": 14}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}, {"MOTIF": 2}])
    s["par_overlap"] = case(hdr={"OPTIONS": T4, "LAYOUT": 4 | (3 << 3)}, loci=[{"HAP2": 3}, {"MOTIF": 2}])
    s["par_shuffled"] = case(hdr={"OPTIONS": T4, "CONTAINER": 1 << 4, "DEPTH": 14}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}])
    s["shuffled_many"] = case(hdr={"CONTAINER": 1 << 4, "DEPTH": 14, "READLEN": 11}, loci=[{"HAP2": 3}, {"TEMPLATE": 2}])
    s["nobamlet_irr"] = case(hdr={"OPTIONS": 0x40}, loci=[{"TYPE": 1 | (2 << 4), "HAP2": 0x80 | 25, "IRR": 4}])
    s["log_warn"] = case(hdr={"OPTIONS": 0x10}); s["log_debug"] = case(hdr={"OPTIONS": 0x20}); s["log_trace"] = case(hdr={"OPTIONS": 0x30})
    s["log_error"] = case(hdr={"OPTIONS": 0x10, "RES": 0x80})
    s["encode0"] = case(hdr={"NUOPT": 1 << 3, "OPTIONS": T4}, loci=[{"HAP2": 3}])
    s["queue1_long"] = case(hdr={"NUOPT": 2 << 6, "OPTIONS": T4, "READLEN": 11, "DEPTH": 14}, loci=[{"HAP2": 3}])
    s["queue64"] = case(hdr={"NUOPT": 3 << 6, "OPTIONS": T8}, loci=[{"HAP2": 3}])
    for b in range(1, 8):
        s[f"rewrite_option_error{b}"] = case(hdr={"NU2": b << 1}, loci=[{"HAP2": 3}])
    # catalog and output writers
    for tpl in (18, 23):                                      # optional sequence absent from the reference: a region of length 0
        s[f"insertion_t{tpl}"] = case(loci=[{"TEMPLATE": tpl, "MISC": 0x10}])
        s[f"insertion_t{tpl}_het"] = case(loci=[{"TEMPLATE": tpl, "MISC": 0x10 | 0x20}])
    s["insertion"] = case(loci=[{"TEMPLATE": 4, "MISC": 0x10}]); s["insertion_hom"] = case(loci=[{"TEMPLATE": 4, "MISC": 0x10 | 0x60}])
    s["swap_neither_ref"] = case(loci=[{"TEMPLATE": 3, "MISC": 2}])
    s["regions_far_apart"] = case(loci=[{"TEMPLATE": 2, "MALFORM": 26}])
    s["contig_Y_nochr"] = case(hdr={"LAYOUT": 0x40}, loci=[{"PLACE": 2}])
    s["scaffold_names"] = case(hdr={"LAYOUT": 0x80}, loci=[{"HAP2": 3}])
    s["target_region_flank1001"] = case(loci=[{"FIELDS": 1, "PLACE": 3 << 2}]); s["target_region_flank1100"] = case(loci=[{"FIELDS": 1, "PLACE": 13 << 2}])
    s["male_X_nodepth"] = case(hdr={"OPTIONS": 1}, loci=[{"PLACE": 1, "COVER": 7}])
    s["smn_alt_deep"] = case(hdr={"DEPTH": 0}, loci=[{"TEMPLATE": 3, "TYPE": 0x40, "MISC": 0x0C}])
    s["smn_alt_shallow"] = case(loci=[{"TEMPLATE": 3, "TYPE": 0x40, "MISC": 0x0C, "COVER": 5}])
    s["smn_ref_second"] = case(loci=[{"TEMPLATE": 3, "TYPE": 0x40, "MISC": 1}])
    s["duplicate_ids"] = case(loci=[{"HAP2": 3}, {"MALFORM": 7, "MOTIF": 2}])
    for ty in (2, 3):
        s[f"plus_type{ty}"] = case(loci=[{"TEMPLATE": 1, "TYPE": ty}])
    s["eight_repeats"] = case(loci=[{"TEMPLATE": 255, "HAP2": 3}]); s["same_motif_twice"] = case(loci=[{"TEMPLATE": 252, "HAP1": 0x80 | 22, "HAP2": 0x80 | 24}])
    s["same_motif_rare"] = case(loci=[{"TEMPLATE": 252, "TYPE": 1 | (1 << 4), "IRR": 3, "HAP2": 0x80 | 24}])
    s["adjacent_same_motif"] = case(hdr={"READLEN": 6}, loci=[{"TEMPLATE": 253, "MOTIF": 5, "HAP2": 0x80 | 16}])
    s["three_same_motif"] = case(loci=[{"TEMPLATE": 254, "MOTIF": 7, "HAP2": 0x80 | 14}])
    s["many_loci_bad_one"] = case(hdr={"NOISE": 5 << 4, "OPTIONS": 1 << 2}, loci=[{"HAP2": 3}, {"MALFORM": 13}])
    s["noise_among_loci"] = case(hdr={"NOISE": (8 << 4) | 12, "OPTIONS": 1 << 2}, loci=[{"HAP2": 3}])
    # pair routing
    s["irr_anchored_offtarget"] = case(loci=[{"TYPE": 1 | (1 << 4), "IRR": 0x80 | 3, "HAP2": 0x80 | 24}])
    s["mate_in_other_offtarget"] = case(loci=[{"HAP2": 3}, {"TYPE": 1 | (2 << 4), "MOTIF": 2}], extras=[[6, 0, 60, 0, 0, 0, 40], [6, 0, 60, 0, 0, 0, 90]])
    s["three_offtargets_ext500"] = case(hdr={"EXT": 2, "NOISE": 9}, loci=[{"TYPE": 1 | (3 << 4), "IRR": 4, "HAP2": 0x80 | 24}])
    s["position_beyond_contig"] = case(extras=[[21, 0, 60, 0, 0, 0, 5]])
    for mod in range(1, 8):
        s[f"extra_mod{mod}"] = case(loci=[{"HAP2": 3}], extras=[[4, 0, 60, 0, 0, 0, 5, mod], [11, 0, 40, 0, 0, 0, 5, mod]])
    s["left_flank_short"] = case(loci=[{"PLACE": 5 << 2}])
    # alignment
    for rl in (11, 12):                                       # long reads with errors: pieces beyond the fast paths' limits
        for err in (2, 3, 4):
            s[f"longreads{rl}_err{err}"] = case(hdr={"READLEN": rl, "ERRORS": err, "DEPTH": 3}, loci=[{"HAP2": 3}])
        s[f"longreads{rl}_ext1500"] = case(hdr={"READLEN": rl, "ERRORS": 2, "EXT": 3, "DEPTH": 3}, loci=[{"HAP2": 3}])
    s["two_mismatches"] = case(hdr={"ERRORS": 3, "DEPTH": 13}, loci=[{"HAP2": 3}])
    s["two_mismatches_del"] = case(hdr={"ERRORS": 3 | 8, "DEPTH": 13}, loci=[{"HAP2": 3}])
    s["read_n_in_repeat"] = case(hdr={"ERRORS": 0x20}, loci=[{"TYPE": 1 | (1 << 4), "IRR": 0x80 | 4, "HAP2": 0x80 | 24}])
    s["overhang_frag1000_ext500"] = case(hdr={"EXT": 2, "FRAG": 9}, loci=[{"HAP2": 3}])
    s["overhang_longread_ext500"] = case(hdr={"EXT": 2, "READLEN": 12, "DEPTH": 3}, loci=[{"HAP2": 3}])
    s["indel_in_homopolymer"] = case(hdr={"ERRORS": 0x18, "DEPTH": 8}, loci=[{"MOTIF": 5, "HAP1": 0x80 | 16, "HAP2": 0x80 | 20}])
    s["short_reads_junction"] = case(hdr={"READLEN": 9, "FRAG": 10, "ERRORS": 3, "DEPTH": 8}, loci=[{"HAP2": 3}])
    s["swap_same_alternatives"] = case(loci=[{"TEMPLATE": 13, "SMALL": 6, "HAP2": 3}])
    s["flank_continues_homopolymer"] = case(hdr={"READLEN": 6}, loci=[{"PLACE": 3 << 6, "MOTIF": 5, "HAP2": 0x80 | 16}])
    # genotyping
    for jit in (1, 2, 3):                                     # stutter: many candidate sizes (the rewrite's genotype grid needs threads >= 2)
        s[f"stutter{jit}"] = case(hdr={"OPTIONS": 1 << 2, "DEPTH": 8}, loci=[{"COVER": jit << 5, "HAP1": 0x80 | 12, "HAP2": 0x80 | 16}])
        s[f"stutter{jit}_homopolymer"] = case(hdr={"OPTIONS": T4, "DEPTH": 8, "ERRORS": 0x18}, loci=[{"COVER": jit << 5, "MOTIF": 5, "HAP1": 0x80 | 10, "HAP2": 0x80 | 18}])
    s["stutter_single_thread"] = case(hdr={"DEPTH": 8}, loci=[{"COVER": 2 << 5, "HAP1": 0x80 | 12, "HAP2": 0x80 | 16}])
    for place, opt in ((2, 0), (1, 1)):                       # haploid: chrY, or chrX in a male
        nm = "Y" if place == 2 else "Xmale"
        s[f"haploid_{nm}_ref"] = case(hdr={"OPTIONS": opt}, loci=[{"PLACE": place, "COVER": 0x80}])
        s[f"haploid_{nm}_zero"] = case(hdr={"OPTIONS": opt}, loci=[{"PLACE": place, "COVER": 0x80, "HAP1": 0x80}])
        s[f"haploid_{nm}_expanded"] = case(hdr={"OPTIONS": opt, "READLEN": 7}, loci=[{"PLACE": place, "COVER": 0x80, "HAP1": 0x80 | 24, "TYPE": 0x11, "IRR": 5}])
        s[f"haploid_{nm}_huge"] = case(hdr={"OPTIONS": opt, "READLEN": 7}, loci=[{"PLACE": place, "COVER": 0x80, "HAP1": 0x80 | 31, "TYPE": 0x11, "IRR": 5}])
        for tpl in (3, 4):
            s[f"haploid_{nm}_small{tpl}"] = case(hdr={"OPTIONS": opt}, loci=[{"PLACE": place, "TEMPLATE": tpl, "MISC": 4}])
    s["allele_error_rate0"] = case(loci=[{"TEMPLATE": 3, "FIELDS": 2, "MISC": 1}]); s["allele_error_rate1"] = case(loci=[{"TEMPLATE": 3, "FIELDS": 2, "MISC": 3}])
    s["allele_lrt_negative"] = case(loci=[{"TEMPLATE": 3, "FIELDS": 4, "MISC": 0x28}])
    s["small_no_reads"] = case(loci=[{"TEMPLATE": 3, "COVER": 7}])
    for fl in (7, 8):
        s[f"right_breakpoint_flank{fl}"] = case(loci=[{"TEMPLATE": 3, "FIELDS": fl << 4, "MISC": 4}]); s[f"right_breakpoint_repeat{fl}"] = case(loci=[{"FIELDS": fl << 4, "HAP2": 3}])
    s["rare_frag1000_ext500"] = case(hdr={"EXT": 2, "FRAG": 9, "ERRORS": 7}, loci=[{"TYPE": 1, "HAP2": 0x80 | 22}])
    s["repeat_with_offtarget_irr"] = case(hdr={"READLEN": 7}, loci=[{"TYPE": 0x10, "IRR": 3}])
    s["repeat_swap_rare_irr"] = case(hdr={"READLEN": 7}, loci=[{"TEMPLATE": 14, "TYPE": 0x11, "IRR": 3}])
    s["irr_short_reads"] = case(hdr={"READLEN": 7}, loci=[{"TYPE": 0x11, "IRR": 3}])
    s["irr_short_reads_extra"] = case(hdr={"READLEN": 7}, loci=[{"TYPE": 0x11, "IRR": 3}], extras=[[20, 0, 60, 8, 3, 0, 5]])
    for m in range(8):                                        # MinimalLocusCoverage: all eight values
        s[f"mincoverage{m}"] = case(loci=[{"MALFORM": 0x80 | ((m & 3) << 5), "COVER": ((m >> 2) << 4) | (4 if m % 2 else 0)}])
    s["worker_exception_threads"] = case(hdr={"OPTIONS": 1 << 2, "NOISE": 9 << 4}, loci=[{"TEMPLATE": 17, "IMPURE": 0x80, "TYPE": 1}])
    s["two_rare"] = case(loci=[{"TEMPLATE": 2, "TYPE": 1, "IMPURE": 0x80}])
    s["rare_plus_repeat"] = case(loci=[{"TEMPLATE": 2, "TYPE": 1 | (1 << 4), "IRR": 3, "HAP2": 0x80 | 24}])
    s["header10"] = case(hdr={"HEADER": 10}, loci=[{"HAP2": 3}])
    s["cache"] = case(hdr={"NU2": 1}, loci=[{"HAP2": 3}])
    s["cache_multi"] = case(hdr={"NU2": 1, "OPTIONS": 4}, loci=[{"HAP2": 3}, {"TEMPLATE": 2, "TYPE": 1 | (2 << 4), "IRR": 2}, {"TEMPLATE": 3}, {"TEMPLATE": 6}])
    s["cache_malformed"] = case(hdr={"NU2": 1}, loci=[{"MALFORM": 3}])
    return s


def main():
    tool, data, seeds = sys.argv[1:4]
    env = dict(os.environ, DFZ_SURFACE="locus", DFZ_WORK=os.path.join(data, ".work"))
    ls = locus_seeds()
    for d in ("locus", "catalog", "sam", "bam"):
        shutil.rmtree(os.path.join(seeds, d), ignore_errors=True)
        os.makedirs(os.path.join(seeds, d))
    shutil.rmtree(os.path.join(data, "base"), ignore_errors=True)
    for name, b in ls.items():
        with open(os.path.join(seeds, "locus", name), "wb") as f:
            f.write(b)
    # base cases: well-formed cases of different shapes
    base = ["het_expanded", "rare_irr", "template02", "loci4", "chrX_male", "template06"][:NBASE]
    tmp = os.path.join(data, ".decode")
    for i, name in enumerate(base):
        out = os.path.join(data, "base", str(i))
        subprocess.run([tool, "decode", os.path.join(seeds, "locus", name), out], env=env, check=True, stdout=subprocess.DEVNULL)
    # raw-surface seeds: byte 0 selects the base case; the rest is the file
    n = {"catalog": 0, "sam": 0, "bam": 0}
    seen = {"catalog": set(), "sam": set(), "bam": set()}

    def add(kind, sel, body, name, opt=0):
        if (body, opt) in seen[kind] or len(body) > 30000:
            return
        seen[kind].add((body, opt))
        with open(os.path.join(seeds, kind, name), "wb") as f:
            f.write(bytes([sel, opt]) + body)
        n[kind] += 1

    L0 = '{"LocusId": "L0", "LocusStructure": "%s", "ReferenceRegion": "chr1:1300-1330", "VariantType": "Repeat"%s}'
    shapes = {"object": L0 % ("(CAG)*", ""), "empty_array": "[]", "blank_array": "[ \n ]", "array_of_number": "[1]",
              "escaped_quote": '[{"LocusId": "L\\"0", "LocusStructure": "(CAG)*", "ReferenceRegion": "chr1:1300-1330", "VariantType": "Repeat"}]',
              "nested_object": "[" + L0 % ("(CAG)*", ', "Extra": {"a": {"b": [1, {"c": "}"}]}}') + "]",
              "truncated_in_object": "[" + (L0 % ("(CAG)*", ""))[:60], "no_closing_bracket": "[" + L0 % ("(CAG)*", ""),
              "trailing_text": L0 % ("(CAG)*", "") + "x", "trailing_text_array": "[" + L0 % ("(CAG)*", "") + "] x",
              "offtargets_as_object": "[" + L0 % ("(CAG)*", ', "OfftargetRegions": {"a": "chr9:500-560"}') + "]",
              "region_as_number": "[" + (L0 % ("(CAG)*", "")).replace('"chr1:1300-1330"', "1300") + "]",
              "structure_no_parens": "[" + L0 % ("ACG*", "") + "]", "structure_empty_alt": "[" + L0 % ("(AC|)", "") + "]",
              "structure_alt_star": "[" + L0 % ("(A|C)*", "") + "]", "structure_three_alts": "[" + L0 % ("(A|C|G)", "") + "]",
              "structure_nested": "[" + L0 % ("((CAG)*)", "") + "]", "structure_empty_parens": "[" + L0 % ("()*", "") + "]",
              "two_contigs": "[" + (L0 % ("(CAG)*CAACAG(CCG)*", "")).replace('"chr1:1300-1330"', '["chr1:1300-1330", "chr9:1336-1340"]').replace('"Repeat"', '["Repeat", "Repeat"]') + "]",
              "bom": "\ufeff[" + L0 % ("(CAG)*", "") + "]", "unicode_escape": "[" + L0 % ("(CAG)*", ', "Note": "\\u00e9\\ud83d"') + "]"}
    for nm, text in shapes.items():
        for th in (0, 1, 2):                                  # --threads 1, 2, 4 (the rewrite slices the file itself from 2)
            add("catalog", 0, text.encode("utf-8", "surrogatepass"), f"shape_{nm}_t{th}", th)
    for name, b in ls.items():
        shutil.rmtree(tmp, ignore_errors=True)
        if subprocess.run([tool, "decode", os.path.join(seeds, "locus", name), tmp], env=env, stdout=subprocess.DEVNULL).returncode:
            continue
        # catalogs fit the base case with the same genome only when the genome is the default one; others are still
        # useful as parser inputs
        if not os.path.exists(os.path.join(tmp, "catalog.json")):   # a case that removes the file on purpose
            continue
        add("catalog", 0, open(os.path.join(tmp, "catalog.json"), "rb").read(), name)
        if name in base:
            # reads of the same case at low depth and with short reads (small enough to mutate), as BAM and as SAM
            i = base.index(name)
            for depth, readlen in ((5, 4), (6, 0), (12, 3), (5, 0), (6, 4)):
                for container, kind in ((0, "bam"), (2, "sam")):
                    b2 = bytearray(b)
                    b2[H["CONTAINER"]] = container
                    b2[H["DEPTH"]], b2[H["READLEN"]], b2[H["FRAG"]] = depth, readlen, 5 if readlen else 0
                    for k in range(len(b2) // 16 - 1):          # the loci's own depth settings off
                        if 16 * (k + 1) + L["COVER"] < len(b2):
                            b2[16 * (k + 1) + L["COVER"]] &= 0x80
                    p2 = os.path.join(data, ".seed_small")
                    open(p2, "wb").write(bytes(b2))
                    shutil.rmtree(tmp, ignore_errors=True)
                    subprocess.run([tool, "decode", p2, tmp], env=env, check=True, stdout=subprocess.DEVNULL)
                    f = os.path.join(tmp, "reads." + kind)
                    body = gzip.open(f, "rb").read() if kind == "bam" else open(f, "rb").read()
                    add(kind, i, body, f"{name}_d{depth}_r{readlen}")
                    if depth == 6:
                        add(kind, i, body, f"{name}_d{depth}_r{readlen}_threads4", 2)
                    os.remove(p2)
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(os.path.join(data, ".work"), ignore_errors=True)
    print(f"seeds: locus {len(ls)}, catalog {n['catalog']}, sam {n['sam']}, bam {n['bam']}; base cases {len(base)}")


if __name__ == "__main__":
    main()
