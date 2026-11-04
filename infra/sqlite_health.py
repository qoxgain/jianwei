"""Classify real SQLite integrity results without confusing contention with corruption."""
import hashlib,json,sqlite3,time

STRUCTURAL_CODES={'SQLITE_CORRUPT','SQLITE_NOTADB'}


def error_category(error):
    code=getattr(error,'sqlite_errorcode',None)
    primary=code&255 if isinstance(code,int) else None
    name=getattr(error,'sqlite_errorname','')
    if primary in {sqlite3.SQLITE_CORRUPT,sqlite3.SQLITE_NOTADB} or name.startswith(tuple(STRUCTURAL_CODES)):return 'structural_corruption'
    if primary==sqlite3.SQLITE_BUSY or name.startswith('SQLITE_BUSY'):return 'busy'
    if primary==sqlite3.SQLITE_LOCKED or name.startswith('SQLITE_LOCKED'):return 'locked'
    if primary==sqlite3.SQLITE_INTERRUPT:return 'interrupted'
    return 'check_error'


def inspect_native(native,*,timeout_seconds,busy_timeout_ms,foreign_keys=True,cancel_event=None):
    """Private cursor only. Never hold an app commit gate waiting on another writer."""
    deadline=time.monotonic()+timeout_seconds;timed_out=False;cancelled=False
    def stop():
        nonlocal timed_out,cancelled
        cancelled=cancel_event is not None and cancel_event.is_set();timed_out=time.monotonic()>=deadline
        return int(timed_out or cancelled)
    result={'status':'failed','check':'integrity_check_and_foreign_keys' if foreign_keys else 'integrity_check',
        'category':'check_error','checked_ns':time.time_ns()}
    cursor=sqlite3.Connection.cursor(native,factory=sqlite3.Cursor)
    # A native Cursor still inherits connection result conversion. Application
    # row/text factories must neither mask corrupt diagnostics as "ok" nor run
    # arbitrary callbacks inside the commit health boundary. Restore afterwards.
    cursor.row_factory=None;previous_text_factory=native.text_factory;native.text_factory=str
    native.set_progress_handler(stop,1000)
    try:
        cursor.execute('PRAGMA busy_timeout = '+str(max(0,min(busy_timeout_ms,int(timeout_seconds*1000)))))
        if stop():return {**result,'category':'cancelled' if cancelled else 'timeout'}
        rows=[r[0] for r in cursor.execute('PRAGMA integrity_check').fetchall()]
        if rows!=['ok']:
            # SQLite also reports logical CHECK/NOT NULL/UNIQUE violations here.
            logical=('CHECK constraint failed','NULL value in','non-unique entry in index')
            category='logical_consistency' if rows and all(str(r).startswith(logical) for r in rows) else 'structural_corruption'
            return {**result,'category':category,'message_count':len(rows),
                'diagnostic_sha256':hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
        if foreign_keys:
            violations=cursor.execute('PRAGMA foreign_key_check').fetchall()
            if violations:return {**result,'category':'foreign_keys','violation_count':len(violations)}
        if stop():return {**result,'category':'cancelled' if cancelled else 'timeout'}
        return {**result,'status':'ok','category':'healthy'}
    except sqlite3.Error as error:
        category='cancelled' if cancelled else 'timeout' if timed_out else error_category(error)
        return {**result,'category':category,'exception_type':type(error).__name__,
            'sqlite_errorname':getattr(error,'sqlite_errorname',None)}
    finally:
        native.set_progress_handler(None,0);native.text_factory=previous_text_factory;cursor.close()


def inspect_file(path,**kwargs):
    # All reads use SQLite's own VFS, never close a separate raw FD for a live DB.
    try:source=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=0)
    except sqlite3.Error as error:return {'status':'failed','category':error_category(error),
        'exception_type':type(error).__name__,'sqlite_errorname':getattr(error,'sqlite_errorname',None)}
    try:return inspect_native(source,**kwargs)
    finally:source.close()
