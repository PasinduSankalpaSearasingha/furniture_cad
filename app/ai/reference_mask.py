"""Conservative foreground extraction, with explicit user correction support."""
import base64
import threading
from io import BytesIO
import cv2
import numpy as np
from PIL import Image

_segmentation_lock=threading.Lock()


def png_bytes(array):
    stream=BytesIO();Image.fromarray(array).save(stream,format='PNG');return stream.getvalue()


def decode_mask(encoded,aspect,size):
    if encoded.startswith('data:image/png;base64,'): encoded=encoded.split(',',1)[1]
    try:
        data=base64.b64decode(encoded,validate=True)
        if len(data)>340000: raise ValueError('Mask file is too large')
        with Image.open(BytesIO(data)) as image:
            if image.format!='PNG' or max(image.size)>1024 or min(image.size)<16:
                raise ValueError('Use a PNG foreground mask between 16 and 1024 pixels')
            if abs(image.width/image.height-aspect)>max(.02,aspect*.025):
                raise ValueError('Mask and reference must have the same aspect ratio')
            return np.array(image.convert('L').resize(size,Image.Resampling.NEAREST))>=128
    except (OSError,ValueError) as exc:
        raise ValueError('Invalid foreground mask: '+str(exc)) from exc


def foreground(image_bytes,mask_png=None,strokes=(),max_side=384):
    with Image.open(BytesIO(image_bytes)) as source:
        original=source.size
        scale=min(1,max_side/max(original));size=tuple(max(16,round(v*scale)) for v in original)
        image=np.array(source.convert('RGB').resize(size,Image.Resampling.LANCZOS))
    if mask_png:
        mask=decode_mask(mask_png,original[0]/original[1],size)
        method='USER_MASK'
    else:
        lab=cv2.cvtColor(image,cv2.COLOR_RGB2LAB).astype(np.float32)
        h,w=image.shape[:2];border=max(2,round(min(h,w)*.035))
        outside=np.ones((h,w),bool);outside[border:-border,border:-border]=False
        samples=lab[outside][::max(1,int(outside.sum()/2500))]
        with _segmentation_lock:
            cv2.setRNGSeed(841)
            _,_,centers=cv2.kmeans(samples,4,None,(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,30,.3),3,cv2.KMEANS_PP_CENTERS)
        distance=np.sqrt(np.min(np.sum((lab[:,:,None,:]-centers[None,None,:,:])**2,axis=-1),axis=2))
        normalized=np.clip(distance*3,0,255).astype(np.uint8)
        threshold,_=cv2.threshold(normalized,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        candidate=(normalized>max(18,threshold)).astype(np.uint8)
        candidate=cv2.morphologyEx(candidate,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        count,labels,stats,_=cv2.connectedComponentsWithStats(candidate)
        if count>1:
            largest=max(stats[1:,cv2.CC_STAT_AREA])
            candidate=np.isin(labels,[i for i in range(1,count) if stats[i,cv2.CC_STAT_AREA]>=max(12,largest*.025)]).astype(np.uint8)
        mask_labels=np.full((h,w),cv2.GC_PR_BGD,np.uint8)
        mask_labels[cv2.dilate(candidate,np.ones((5,5),np.uint8))>0]=cv2.GC_PR_FGD
        mask_labels[cv2.erode(candidate,np.ones((3,3),np.uint8))>0]=cv2.GC_FGD
        mask_labels[[0,-1],:]=cv2.GC_BGD;mask_labels[:,[0,-1]]=cv2.GC_BGD
        for stroke in strokes:
            data=stroke.model_dump() if hasattr(stroke,'model_dump') else stroke
            points=np.array([[round(x*(w-1)),round(y*(h-1))] for x,y in data['points']],np.int32)
            value=cv2.GC_FGD if data['kind']=='foreground' else cv2.GC_BGD
            radius=max(1,round(data['radius']*min(w,h)))
            cv2.polylines(mask_labels,[points],False,value,2*radius)
            for point in points:cv2.circle(mask_labels,tuple(point),radius,value,-1)
        if (mask_labels==cv2.GC_FGD).sum()<8:
            mask=np.zeros((h,w),bool)
        else:
            with _segmentation_lock:
                cv2.setRNGSeed(841)
                cv2.grabCut(image,mask_labels,None,np.zeros((1,65),np.float64),np.zeros((1,65),np.float64),4,cv2.GC_INIT_WITH_MASK)
            mask=np.isin(mask_labels,[cv2.GC_FGD,cv2.GC_PR_FGD])
        method='USER_GUIDED_GRABCUT' if strokes else 'AUTOMATIC_GRABCUT'
    fraction=float(mask.mean());h,w=mask.shape
    border_touch=float((mask[0].sum()+mask[-1].sum()+mask[:,0].sum()+mask[:,-1].sum())/(2*w+2*h))
    usable=.015<fraction<.85 and border_touch<.12
    warnings=[]
    if method=='AUTOMATIC_GRABCUT': warnings.append('Automatic foreground estimate; inspect and correct the mask before relying on the fit.')
    if not usable:warnings.append('Foreground is empty, clipped or ambiguous; correct the mask before geometry fitting.')
    overlay=image.copy();overlay[mask]=(image[mask]*.6+np.array([40,190,120])*.4).astype(np.uint8)
    return dict(image=image,mask=mask,original_size=original,method=method,usable=usable,
                foreground_fraction=fraction,border_touch=border_touch,warnings=warnings,overlay=overlay)
