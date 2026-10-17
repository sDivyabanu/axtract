"""Bounded diagnostic on the six existing scanned PDFs; not adapter accuracy.

Uses the supplied segmenter and baseline OCR boxes. Original experiment crop
manifest was unavailable: regenerated crops are recorded explicitly. No expected
answers or synthetic PDFs are generated. Historical lossy cleanup is diagnostic.
"""
from __future__ import annotations
import argparse,hashlib,json,os,sys,time
import pymupdf
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"));sys.path.insert(0,str(ROOT/"scripts"))
from models.document import DocumentBlock
from services.preview_service import render_page_pil
from services.scanned_math import available_memory_bytes,_overlap
from services.vision import pix2text_mfr
from services.vision.line_math import tidy_latex,segment_lines,looks_like_prose
from services.vision.latex import validate_latex,looks_mathy
from table_accuracy_sample import process_peak_working_set


def sha(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):h.update(chunk)
    return h.hexdigest()


def run(args):
    args.output.mkdir(parents=True,exist_ok=False)
    source=json.loads((args.baseline/"performance.json").read_text(encoding="utf-8"))
    pdfs={p:h for p,h in source["expected_pdfs"].items() if p.startswith("old_scans_math/")}
    assert len(pdfs)==6,"Expected the original six scanned PDFs"
    rows=[]
    variants=("raw","legacy_cleanup","safe_cleanup")
    for pdf,expected in pdfs.items():
        assert sha(args.data_root/"pdfs"/pdf)==expected
        record=next(json.loads(p.read_text(encoding="utf-8")) for p in args.baseline.glob("document_*.json")
                    if json.loads(p.read_text(encoding="utf-8"))["pdf"]==pdf)
        image=render_page_pil(args.data_root/"pdfs"/pdf,1,200)
        blocks=[DocumentBlock.model_validate(b) for b in record["blocks"]]
        # Prototype reconstruction deliberately precedes production table/route
        # exclusions. It can therefore expose why an experiment outscored routing.
        with pymupdf.open(args.data_root/"pdfs"/pdf) as document:
            page=document[0];pw,ph=page.rect.width,page.rect.height
            native=[(w[0]/pw,w[1]/ph,w[2]/pw,w[3]/ph,*w[4:]) for w in page.get_text("words")]
        width,height=image.size;selected=[]
        for x0,y0,x1,y1 in segment_lines(image):
            box=(max(0,(x0-6)/width),max(0,(y0-6)/height),min(1,(x1+7)/width),min(1,(y1+7)/height))
            if box[3]-box[1]>.18:continue
            covered=[b for b in blocks if b.bbox and _overlap(box,b.bbox)>.35 and b.extractor in ("rapidocr","pymupdf")]
            ocr=" ".join(w[4] for w in native if box[0]<=(w[0]+w[2])/2<=box[2] and box[1]<=(w[1]+w[3])/2<=box[3]) if native else " ".join(b.content for b in covered)
            if not looks_like_prose(ocr):selected.append((box,ocr,covered))
        chosen=[];texts={v:[] for v in variants};started=time.monotonic()
        for index,(box,ocr,covered) in enumerate(selected):
            ram=available_memory_bytes()
            if time.monotonic()-started>=60 or index>=48 or (ram is not None and ram<350*1024**2):break
            width,height=image.size
            crop=image.crop((int(box[0]*width),int(box[1]*height),int(box[2]*width),int(box[3]*height)))
            prediction=pix2text_mfr.recognize(crop,max_seconds=min(8,60-(time.monotonic()-started)),max_tokens=200)
            raw=prediction.latex
            values={"raw":raw,"legacy_cleanup":tidy_latex(raw,preserve_semantics=False),"safe_cleanup":tidy_latex(raw,preserve_semantics=True)}
            for variant,latex in values.items():
                if variant=="raw" or (validate_latex(latex).ok and looks_mathy(latex)
                                      and (variant!="safe_cleanup" or prediction.completed)):
                    texts[variant].append("$$\n"+latex+"\n$$")
            chosen.append({"bbox":box,"ocr_text":ocr,"prediction":vars(prediction),
                           "valid":{v:validate_latex(t).ok for v,t in values.items()}})
        elapsed=time.monotonic()-started
        rows.append({"pdf":pdf,"seconds":elapsed,"pages":1,"errors":[],"segmented_candidates":len(selected),
                     "attempted_crops":len(chosen),"crops":chosen,"peak_working_set_bytes":process_peak_working_set()})
        for variant in variants:
            path=args.output/variant/Path(pdf).parent/(Path(pdf).stem+"_pg1_repeat1.md")
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open("x",encoding="utf-8") as f:f.write("\n\n".join(texts[variant]))
        print(pdf,"crops",len(chosen),"of",len(selected),"seconds",round(elapsed,3),flush=True)
    common={"mode":"regenerated_line_crop_experiment_not_pipeline","expected_pdfs":pdfs,
            "case_source_hashes":source["case_source_hashes"],"configuration":{"render_dpi":200,"max_page_seconds":60,
             "max_crops":48,"max_tokens":200,"original_crop_manifest_available":False},"documents":rows,
            "model_hashes":{f:sha(pix2text_mfr.MFR_DIR/f) for f in ("encoder_model.onnx","decoder_model.onnx","tokenizer.json")}}
    for variant in variants:
        with (args.output/variant/"performance.json").open("x",encoding="utf-8") as f:json.dump({**common,"variant":variant},f,indent=2)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root",type=Path,required=True);p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    run(p.parse_args())
if __name__=="__main__":main()
