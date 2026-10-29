import {cloneElement,useId} from 'react';
import type {ReactElement} from 'react';

/** Keep option values and help text out of the control's accessible name. */
export default function FormField({label,help,children}:{label:string;help?:string;children:ReactElement<{id?:string;'aria-describedby'?:string}>}){
 const generated=useId(),id=children.props.id??generated,description=help?`${id}-help`:undefined;
 return <div className="field"><label htmlFor={id}><span>{label}</span></label>{cloneElement(children,{id,...(description?{'aria-describedby':description}:{})})}{help&&<small id={description}>{help}</small>}</div>;
}
