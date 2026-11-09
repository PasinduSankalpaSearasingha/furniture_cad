"""Bounded raster inputs and private, revision-linked image artifacts. No URL fetching."""
import base64
import hashlib
from io import BytesIO
import warnings
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from app.database import ImageAsset, audit
from app.pipeline import safe_directory, file_hash, canonical_hash

Image.MAX_IMAGE_PIXELS = 16_000_000


class ImageInputError(ValueError): pass


def normalized_png(data: bytes, max_bytes: int):
    if not data or len(data) > max_bytes: raise ImageInputError('Image byte limit exceeded')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in ('PNG','JPEG','WEBP') or getattr(source,'n_frames',1) != 1:
                    raise ImageInputError('Only single-frame PNG, JPEG and WebP are supported')
                if max(source.size) > 8192 or source.width*source.height > 16_000_000:
                    raise ImageInputError('Image pixel limit exceeded')
                source.load()
                image = source.convert('RGB')
                result = BytesIO()
                image.save(result,format='PNG')  # Strip EXIF, profiles and filenames.
                encoded = result.getvalue()
                if len(encoded) > 20*1024*1024: raise ImageInputError('Normalized image exceeds storage limit')
                return encoded,image.width,image.height
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning) as exc:
        raise ImageInputError('Invalid or oversized raster image') from exc


def store_image(session,config,project_id,user_id,data,kind='REFERENCE',provenance=None):
    encoded,width,height = normalized_png(data,config.max_image_bytes if kind=='REFERENCE' else 20*1024*1024)
    digest = hashlib.sha256(encoded).hexdigest()
    provenance = provenance or {'source':'USER_PROVIDED','authoritative_geometry':False}
    if kind == 'REFERENCE':
        from app.sample_dimensions import match_dimensions
        provenance = {**provenance, 'dimension_match': match_dimensions(encoded)}
    for existing in session.scalars(select(ImageAsset).where(ImageAsset.project_id==project_id,
                                    ImageAsset.sha256==digest,ImageAsset.kind==kind)):
        if existing.provenance == provenance:
            read_image(existing,config)
            return existing
    record = ImageAsset(project_id=project_id,created_by=user_id,kind=kind,sha256=digest,
                        byte_count=len(encoded),width=width,height=height,provenance=provenance)
    session.add(record)
    session.flush()
    directory = safe_directory(config.storage,'images')
    directory.mkdir(exist_ok=True)
    target = safe_directory(directory,record.id+'.png')
    with target.open('xb') as output: output.write(encoded)
    audit(session,user_id,'image_stored',record.id,kind=kind,sha256=digest,project_id=project_id)
    return record


def image_path(record,config):
    return safe_directory(safe_directory(config.storage,'images'),record.id+'.png')


def read_image(record,config):
    path = image_path(record,config)
    if file_hash(path) != record.sha256: raise ImageInputError('Image integrity verification failed')
    return path.read_bytes()


def image_content(records,config):
    return [{'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(read_image(r,config)).decode(),
             'detail':'high'} for r in records]


def image_json(record):
    return dict(id=record.id,project_id=record.project_id,kind=record.kind,sha256=record.sha256,
                width=record.width,height=record.height,provenance=record.provenance,
                download_url=f'/api/v1/images/{record.id}')
