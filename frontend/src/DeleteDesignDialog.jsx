import {useEffect,useId,useRef} from 'react';

export default function DeleteDesignDialog({design,busy,error,onCancel,onDelete}){
  const dialog=useRef(null),heading=useId(),description=useId();
  useEffect(()=>{dialog.current.showModal();},[]);
  return <dialog ref={dialog} className="delete-dialog" aria-labelledby={heading} aria-describedby={description} onCancel={event=>{event.preventDefault();if(!busy)onCancel();}}>
    <form onSubmit={event=>{event.preventDefault();if(!busy)onDelete();}}>
      <h2 id={heading}>Delete design?</h2>
      <p id={description}>Permanently delete <strong>{design.name}</strong> and its project, saved versions, reference photos and generated CAD files?</p>
      <p className="fine-print">This cannot be undone.</p>
      {error&&<p className="message error" role="alert">{error}</p>}
      <div className="delete-dialog-actions"><button className="secondary" type="button" disabled={busy} onClick={onCancel} autoFocus>Cancel</button><button className="danger" type="submit" disabled={busy}>{busy?'Deleting…':'Delete permanently'}</button></div>
    </form>
  </dialog>;
}
