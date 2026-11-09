// Reduce large camera/product images to the API's existing raster limits.
export async function prepareImage(file){
  if(!['image/png','image/jpeg','image/webp'].includes(file.type))throw Error('Use a PNG, JPG or WebP image.');
  if(file.size>25*1024*1024)throw Error('Use an image smaller than 25 MB.');
  const bitmap=await createImageBitmap(file,{imageOrientation:'from-image'});
  try{
    if(file.size<=1500*1024&&bitmap.width<=8192&&bitmap.height<=8192&&bitmap.width*bitmap.height<=16000000)return file;
    const ratio=Math.min(1,1600/Math.max(bitmap.width,bitmap.height));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*ratio));canvas.height=Math.max(1,Math.round(bitmap.height*ratio));
    const context=canvas.getContext('2d');context.fillStyle='#fff';context.fillRect(0,0,canvas.width,canvas.height);context.drawImage(bitmap,0,0,canvas.width,canvas.height);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.88));
    if(!blob||blob.size>1500*1024)throw Error('Could not reduce this image for upload. Please use a smaller image.');
    return new File([blob],file.name.replace(/\.[^.]+$/,'')+'.jpg',{type:'image/jpeg'});
  }finally{bitmap.close();}
}
