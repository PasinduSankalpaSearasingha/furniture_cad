export default function PhotoMatch({engineering,jobId,reference,references=[]}){
  const review=engineering?.visual_review?.visual;
  const manual=engineering?.workflow?.manual_reconstruction;
  const needsRevision=review?.accepted===false||engineering?.photo_match?.accepted===false;
  return <section className="card photo-match" aria-label="Photo comparison">
    <div className="section-heading"><div><h2>Compare with your photo</h2><p>Check the silhouette, curves, supports and connections against the uploaded furniture.</p></div><span className="pill">{needsRevision?'Closer match needed':review?.accepted?'Visual review completed':'Visual review required'}</span></div>
    <div className="photo-match-images">
      {reference&&<figure><img src={reference.download_url} alt="Original uploaded furniture"/><figcaption>Uploaded photo</figcaption></figure>}
      <figure><img src={`/api/v1/jobs/${jobId}/files/cad_preview.png`} alt="Actual exported CAD from the reference camera"/><figcaption>Actual CAD model</figcaption></figure>
    </div>
    {references.length>1&&<div className="reference-views" aria-label="Additional reference angles">{references.filter(item=>item.id!==reference?.id).map((item,index)=><figure className="reference-view" key={item.id}><img src={item.download_url} alt={`Additional furniture angle ${index+1}`}/><figcaption>Reference angle {index+2}</figcaption></figure>)}</div>}
    {needsRevision&&<div className="message warning" role="status"><div><strong>{manual?'Review this local reconstruction against your photos.':'This draft still differs from your photo.'}</strong><p>{manual?'The reference interpretation has not received AI or physical design approval.':'Review the differences below before using the model.'}</p></div></div>}
    {needsRevision&&review?.findings?.map((finding,index)=><p className="fine-print" key={index}>{finding}</p>)}
    <p className="fine-print">The comparison uses rendered CAD geometry. A visual review does not verify unseen surfaces or an exact reconstruction.</p>
  </section>;
}
